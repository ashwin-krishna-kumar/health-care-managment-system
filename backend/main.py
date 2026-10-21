"""HealthRecords API: wallet-bound accounts, contract consent, encrypted IPFS.

Run from the existing project root with ``python -m uvicorn backend.main:app``.
The original reports table and encrypted files are retained for legacy reads.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Literal
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

import httpx
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from web3 import Web3
from web3.exceptions import ContractLogicError

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
DATA_DIR = Path(os.getenv("HEALTHRECORDS_DATA_DIR", str(BASE_DIR / "data")))
ENCRYPTED_DIR = DATA_DIR / "encrypted"
DATABASE_PATH = DATA_DIR / "reports.db"
RPC_URL = os.getenv("RPC_URL", "http://127.0.0.1:8545")
CONTRACT_ADDRESS = os.getenv("CONTRACT_ADDRESS", "").strip()
FERNET_KEY = os.getenv("FERNET_KEY", "").strip()
IPFS_API_URL = os.getenv("IPFS_API_URL", "http://127.0.0.1:5001/api/v0").rstrip("/")
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "10"))
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
MAX_ENCRYPTED_BYTES = 4 * ((MAX_FILE_SIZE_BYTES + 128 + 2) // 3) + 1024
SESSION_SECONDS = int(os.getenv("SESSION_SECONDS", "3600"))
COOKIE_NAME = "healthrecords_session"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
EXPECTED_CHAIN_ID = int(os.getenv("EXPECTED_CHAIN_ID", "31337"))
PASSWORD_ITERATIONS = 600_000
FRONTEND_ORIGINS = [origin.strip().rstrip("/") for origin in os.getenv(
    "FRONTEND_ORIGINS", "http://127.0.0.1:5500,http://localhost:5500"
).split(",") if origin.strip()]
Role = Literal["patient", "doctor", "admin"]

# These are read methods only. Transactions remain signed by the user's wallet.
CONTRACT_ABI = [
    {"type": "function", "name": "admin", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "address"}]},
    {"type": "function", "name": "verifiedProviders", "stateMutability": "view",
     "inputs": [{"name": "provider", "type": "address"}],
     "outputs": [{"name": "", "type": "bool"}]},
    {"type": "function", "name": "hasAccess", "stateMutability": "view",
     "inputs": [{"name": "patient", "type": "address"}, {"name": "doctor", "type": "address"}],
     "outputs": [{"name": "", "type": "bool"}]},
    {"type": "function", "name": "getMedicalRecord", "stateMutability": "view",
     "inputs": [{"name": "recordId", "type": "uint256"}],
     "outputs": [{"name": "", "type": "tuple", "components": [
         {"name": "id", "type": "uint256"}, {"name": "patient", "type": "address"},
         {"name": "fileHash", "type": "bytes32"}, {"name": "metadataHash", "type": "bytes32"},
         {"name": "createdAt", "type": "uint64"}]}]},
    {"type": "event", "name": "RecordAccessed", "anonymous": False, "inputs": [
        {"name": "accessId", "type": "uint256", "indexed": True},
        {"name": "recordId", "type": "uint256", "indexed": True},
        {"name": "doctor", "type": "address", "indexed": True},
        {"name": "patient", "type": "address", "indexed": False},
        {"name": "emergencyAccess", "type": "bool", "indexed": False},
        {"name": "timestamp", "type": "uint64", "indexed": False}]},
]

app = FastAPI(title="HealthRecords — Login and Encrypted IPFS API", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=FRONTEND_ORIGINS,
                   allow_credentials=True, allow_methods=["GET", "POST"],
                   allow_headers=["Content-Type"], expose_headers=["Content-Disposition"])


@app.middleware("http")
async def protect_browser_requests(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin not in FRONTEND_ORIGINS:
        return JSONResponse({"detail": "This website origin is not allowed."}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@contextmanager
def database():
    connection = sqlite3.connect(DATABASE_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_database() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ENCRYPTED_DIR.mkdir(exist_ok=True)
    with database() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, full_name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL, wallet_address TEXT NOT NULL UNIQUE,
            role TEXT NOT NULL CHECK(role IN ('patient','doctor','admin')), created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS auth_challenges (
            id TEXT PRIMARY KEY, wallet_address TEXT NOT NULL, email TEXT NOT NULL,
            role TEXT NOT NULL, action TEXT NOT NULL, message TEXT NOT NULL,
            chain_scope TEXT NOT NULL, expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, chain_scope TEXT NOT NULL,
            expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rate_limits (
            bucket TEXT PRIMARY KEY, hits INTEGER NOT NULL, started_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS stored_reports (
            chain_scope TEXT NOT NULL, patient_address TEXT NOT NULL, record_id INTEGER NOT NULL,
            file_hash TEXT NOT NULL, original_filename TEXT NOT NULL, content_type TEXT NOT NULL,
            cid TEXT, encrypted_sha256 TEXT, file_size INTEGER NOT NULL, storage_backend TEXT NOT NULL,
            stored_filename TEXT, created_at INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT 'ready', upload_id TEXT,
            PRIMARY KEY(chain_scope, patient_address, record_id)
        );
        CREATE TABLE IF NOT EXISTS audit_receipts (
            tx_hash TEXT NOT NULL, user_id TEXT NOT NULL, record_id INTEGER NOT NULL,
            chain_scope TEXT NOT NULL, used_at INTEGER NOT NULL,
            PRIMARY KEY(chain_scope, tx_hash)
        );
        CREATE INDEX IF NOT EXISTS session_expiry ON sessions(expires_at);
        """)


initialize_database()


def normalize_address(value: str, label: str = "wallet address") -> str:
    if not Web3.is_address(value):
        raise HTTPException(400, f"Enter a valid {label}.")
    return Web3.to_checksum_address(value)


def normalized_hash(value: str) -> str:
    value = value.lower().strip().removeprefix("0x")
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise HTTPException(400, "Invalid file hash.")
    return "0x" + value


def keccak_hex(data: bytes) -> str:
    return "0x" + bytes(Web3.keccak(data)).hex()


def normalized_email(value: str) -> str:
    value = value.strip().lower()
    if len(value) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise HTTPException(400, "Enter a valid email address.")
    return value


def get_fernet() -> Fernet:
    try:
        return Fernet(FERNET_KEY.encode("ascii"))
    except (ValueError, TypeError, UnicodeError) as exc:
        raise HTTPException(503, "Set a valid FERNET_KEY in backend/.env. Keep the existing key for older reports.") from exc


@dataclass
class ChainContext:
    web3: object
    contract: object
    scope: str
    chain_id: int
    address: str


def chain_context() -> ChainContext:
    if not Web3.is_address(CONTRACT_ADDRESS):
        raise HTTPException(503, "Set CONTRACT_ADDRESS in backend/.env to your deployed HealthRecords address.")
    try:
        web3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 5}))
        if not web3.is_connected():
            raise ValueError("Node unavailable")
        chain_id = web3.eth.chain_id
        if chain_id != EXPECTED_CHAIN_ID:
            raise HTTPException(503, f"Expected chain {EXPECTED_CHAIN_ID}; the backend is connected to {chain_id}.")
        address = Web3.to_checksum_address(CONTRACT_ADDRESS)
        if not web3.eth.get_code(address):
            raise HTTPException(503, "No contract at CONTRACT_ADDRESS. Deploy HealthRecords and update backend/.env.")
        genesis_hash = bytes(web3.eth.get_block(0)["hash"]).hex()
        scope = f"{chain_id}:{address.lower()}:{genesis_hash}"
        return ChainContext(web3, web3.eth.contract(address=address, abi=CONTRACT_ABI), scope, chain_id, address)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "Cannot reach the blockchain. Keep npx hardhat node running.") from exc


def chain_call(function):
    try:
        return function.call()
    except Exception as exc:
        raise HTTPException(503, "The smart-contract check failed. Confirm the node and deployed address.") from exc


def verify_signature(wallet: str, message: str, signature: str) -> None:
    try:
        recovered = Account.recover_message(encode_defunct(text=message), signature=signature)
    except Exception as exc:
        raise HTTPException(401, "Invalid wallet signature.") from exc
    if recovered.lower() != wallet.lower():
        raise HTTPException(401, "Sign with the wallet linked to this account.")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


DUMMY_PASSWORD_HASH = hash_password("not-a-real-account-password")


def rate_limit(request: Request, purpose: str, identity: str = "", maximum: int = 30, seconds: int = 600):
    host = request.client.host if request.client else "unknown"
    bucket = hashlib.sha256(f"{host}|{purpose}|{identity}".encode()).hexdigest()
    now = int(time.time())
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM rate_limits WHERE bucket = ?", (bucket,)).fetchone()
        if row and row["started_at"] > now - seconds:
            if row["hits"] >= maximum:
                raise HTTPException(429, "Too many attempts. Wait a few minutes before trying again.")
            db.execute("UPDATE rate_limits SET hits = hits + 1 WHERE bucket = ?", (bucket,))
        else:
            db.execute("INSERT OR REPLACE INTO rate_limits VALUES (?, 1, ?)", (bucket, now))
        db.execute("DELETE FROM rate_limits WHERE started_at < ?", (now - 86400,))


class ChallengeInput(BaseModel):
    wallet_address: str = Field(max_length=64)
    email: str = Field(max_length=254)
    role: Role
    action: Literal["register", "login"]
    contract_address: str = Field(max_length=64)


class CredentialsInput(BaseModel):
    challenge_id: str = Field(max_length=128)
    signature: str = Field(max_length=256)
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128)
    wallet_address: str = Field(max_length=64)
    role: Role
    full_name: str = Field(default="", max_length=100)


def consume_challenge(payload: CredentialsInput, action: str, context: ChainContext):
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM auth_challenges WHERE id = ?", (payload.challenge_id,)).fetchone()
        if row:
            db.execute("DELETE FROM auth_challenges WHERE id = ?", (payload.challenge_id,))
    if row is None or row["expires_at"] <= time.time():
        raise HTTPException(401, "The wallet challenge expired or was already used. Try signing in again.")
    wallet = normalize_address(payload.wallet_address)
    email = normalized_email(payload.email)
    if (row["action"] != action or row["role"] != payload.role or row["email"] != email
            or row["wallet_address"] != wallet.lower() or row["chain_scope"] != context.scope):
        raise HTTPException(401, "The wallet challenge does not match this sign-in request.")
    verify_signature(wallet, row["message"], payload.signature)
    return wallet, email


def role_status(user: dict, context: ChainContext) -> str:
    wallet = normalize_address(user["wallet_address"])
    if user["role"] == "admin":
        if chain_call(context.contract.functions.admin()).lower() != wallet.lower():
            raise HTTPException(403, "This wallet is no longer the contract administrator.")
        return "active"
    if user["role"] == "doctor":
        return "verified" if chain_call(context.contract.functions.verifiedProviders(wallet)) else "pending"
    return "active"


def public_user(user: dict, context: ChainContext) -> dict:
    return {key: user[key] for key in ("id", "full_name", "email", "wallet_address", "role")} | {
        "status": role_status(user, context), "contract_address": context.address, "chain_id": context.chain_id}


def create_session(user: dict, context: ChainContext) -> JSONResponse:
    identity = public_user(user, context)
    token = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + SESSION_SECONDS
    with database() as db:
        db.execute("DELETE FROM sessions WHERE expires_at <= ?", (int(time.time()),))
        db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)",
                   (hashlib.sha256(token.encode()).hexdigest(), user["id"], context.scope, expires_at))
    response = JSONResponse({"user": identity, "expires_at": expires_at})
    response.set_cookie(COOKIE_NAME, token, httponly=True, secure=COOKIE_SECURE,
                        samesite="lax", max_age=SESSION_SECONDS, path="/")
    return response


def current_user(request: Request) -> dict:
    token = request.cookies.get(COOKIE_NAME, "")
    if not token or len(token) > 128:
        raise HTTPException(401, "Sign in to continue.")
    with database() as db:
        row = db.execute("""SELECT users.*, sessions.chain_scope, sessions.expires_at
            FROM sessions JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ?""", (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    if row is None or row["expires_at"] <= time.time():
        raise HTTPException(401, "Your session expired. Sign in again.")
    context = chain_context()
    if row["chain_scope"] != context.scope:
        raise HTTPException(401, "The local blockchain changed. Sign in again after deployment.")
    user = dict(row)
    user["status"] = role_status(user, context)
    user["context"] = context
    return user


@app.get("/")
def root():
    return {"message": "HealthRecords API is running.", "documentation": "/docs"}


def ipfs_endpoint(command: str) -> str:
    """Accept the node's base URL as well as its complete Kubo RPC base."""
    value = IPFS_API_URL.strip().rstrip("/")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc or parts.query or parts.fragment:
        raise HTTPException(503, "Set IPFS_API_URL to http://127.0.0.1:5001/api/v0 in backend/.env.")
    path = parts.path.rstrip("/")
    if not path:
        path = "/api/v0"
    elif not path.endswith("/api/v0"):
        raise HTTPException(503, "IPFS_API_URL must be the Kubo RPC base ending in /api/v0, not a gateway or Web UI URL.")
    return urlunsplit((parts.scheme, parts.netloc, path + "/" + command, "", ""))


def ipfs_connection_status() -> dict:
    """Return an actionable error without disclosing private connection details."""
    try:
        with httpx.Client(timeout=2, trust_env=False) as client:
            response = client.post(ipfs_endpoint("version"))
            response.raise_for_status()
            version = response.json().get("Version")
            if not isinstance(version, str) or not version.strip():
                raise ValueError("Missing Kubo version")
        return {"connected": True, "version": version, "error": None}
    except HTTPException as exc:
        message = exc.detail
    except httpx.TimeoutException:
        message = "IPFS did not respond. Open IPFS Desktop or keep ipfs daemon running, then retry."
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {401, 403}:
            message = "The IPFS node refused this backend. Use your local Kubo RPC API on port 5001."
        else:
            message = "The IPFS RPC endpoint returned an error. Use port 5001 and /api/v0, then retry."
    except httpx.HTTPError:
        message = "Cannot reach IPFS. Open IPFS Desktop or run ipfs daemon; its RPC API normally uses port 5001."
    except (ValueError, TypeError, AttributeError):
        message = "This endpoint is not a Kubo RPC API. Use http://127.0.0.1:5001/api/v0, not the port 8080 gateway."
    return {"connected": False, "version": None, "error": message}


@app.get("/health")
def health():
    connected = False
    contract_configured = False
    deployed_address = ""
    try:
        context = chain_context()
        connected = contract_configured = True
        deployed_address = context.address
    except HTTPException:
        pass
    try:
        get_fernet()
        encryption = True
    except HTTPException:
        encryption = False
    ipfs = ipfs_connection_status()
    return {"status": "ok", "hardhat_connected": connected, "contract_configured": contract_configured,
            "contract_address": deployed_address, "encryption_configured": encryption,
            "ipfs_connected": ipfs["connected"], "ipfs_version": ipfs["version"],
            "ipfs_error": ipfs["error"], "storage_backend": "ipfs", "max_file_size_mb": MAX_FILE_SIZE_MB}


@app.post("/auth/challenge")
def auth_challenge(payload: ChallengeInput, request: Request):
    rate_limit(request, "challenge", maximum=60)
    wallet = normalize_address(payload.wallet_address)
    email = normalized_email(payload.email)
    context = chain_context()
    if normalize_address(payload.contract_address, "contract address") != context.address:
        raise HTTPException(409, "The webpage and backend use different contract addresses. Update backend/.env and restart it.")
    origin = request.headers.get("origin") or FRONTEND_ORIGINS[0]
    nonce = secrets.token_hex(24)
    now = int(time.time())
    message = ("HealthRecords account authentication\n"
               f"Action: {payload.action}\nOrigin: {origin}\nWallet: {wallet}\n"
               f"Email: {email}\nRole: {payload.role}\nChain ID: {context.chain_id}\n"
               f"Contract: {context.address}\nNonce: {nonce}\nIssued at: {now}\nExpires at: {now + 300}\n"
               "This signature authenticates your account; it does not send a transaction.")
    challenge_id = uuid4().hex
    with database() as db:
        db.execute("DELETE FROM auth_challenges WHERE expires_at <= ?", (now,))
        db.execute("INSERT INTO auth_challenges VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (challenge_id, wallet.lower(), email, payload.role, payload.action, message, context.scope, now + 300))
    return {"challenge_id": challenge_id, "message": message, "expires_at": now + 300}


@app.post("/auth/register")
def register(payload: CredentialsInput, request: Request):
    rate_limit(request, "register", maximum=12, seconds=3600)
    context = chain_context()
    wallet, email = consume_challenge(payload, "register", context)
    name = payload.full_name.strip()
    if not name or len(payload.password) < 12:
        raise HTTPException(400, "Enter your name and a password of at least 12 characters.")
    admin = chain_call(context.contract.functions.admin()).lower()
    if payload.role == "admin" and wallet.lower() != admin:
        raise HTTPException(403, "Only the wallet that is the contract administrator can create an admin account.")
    if payload.role != "admin" and wallet.lower() == admin:
        raise HTTPException(403, "This is the administrator wallet. Select Administrator or switch to another wallet.")
    if payload.role == "patient" and chain_call(context.contract.functions.verifiedProviders(wallet)):
        raise HTTPException(403, "This wallet is a verified doctor. Select Doctor or use your patient wallet.")
    user = {"id": uuid4().hex, "full_name": name, "email": email, "wallet_address": wallet.lower(),
            "role": payload.role, "password_hash": hash_password(payload.password), "created_at": int(time.time())}
    try:
        with database() as db:
            db.execute("INSERT INTO users VALUES (:id, :full_name, :email, :password_hash, :wallet_address, :role, :created_at)", user)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "An account already uses that email or wallet. Sign in with the existing account.") from exc
    return create_session(user, context)


@app.post("/auth/login")
def login(payload: CredentialsInput, request: Request):
    email = normalized_email(payload.email)
    rate_limit(request, "login", email, maximum=10)
    context = chain_context()
    wallet, email = consume_challenge(payload, "login", context)
    with database() as db:
        row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    valid_password = verify_password(payload.password, row["password_hash"] if row else DUMMY_PASSWORD_HASH)
    if row is None or not valid_password or row["wallet_address"] != wallet.lower() or row["role"] != payload.role:
        raise HTTPException(401, "Email, password, wallet, or selected role did not match.")
    return create_session(dict(row), context)


@app.post("/auth/logout")
def logout(request: Request):
    token = request.cookies.get(COOKIE_NAME, "")
    with database() as db:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),))
    response = JSONResponse({"message": "Signed out."})
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=COOKIE_SECURE, samesite="lax")
    return response


@app.get("/auth/me")
def me(user: dict = Depends(current_user)):
    return {"user": public_user(user, user["context"])}


@app.get("/admin/doctors")
def doctors(user: dict = Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "Administrator access required.")
    with database() as db:
        rows = db.execute("SELECT id, full_name, email, wallet_address, created_at FROM users WHERE role = 'doctor' ORDER BY created_at DESC").fetchall()
    result = []
    for row in rows:
        doctor = dict(row)
        doctor["verified"] = bool(chain_call(user["context"].contract.functions.verifiedProviders(
            normalize_address(doctor["wallet_address"]))))
        result.append(doctor)
    return {"doctors": result}


def anchored_record(context: ChainContext, record_id: int):
    if not 1 <= record_id <= 2**63 - 1:
        raise HTTPException(400, "Enter a positive record ID.")
    try:
        return context.contract.functions.getMedicalRecord(record_id).call()
    except ContractLogicError as exc:
        raise HTTPException(404, "This record does not exist on the current blockchain.") from exc
    except Exception as exc:
        raise HTTPException(503, "Could not check the anchored record on the blockchain.") from exc


def check_record(context: ChainContext, record_id: int, patient: str, expected_hash: str | None = None):
    record = anchored_record(context, record_id)
    if record[1].lower() != patient.lower():
        raise HTTPException(403, "This record belongs to a different patient.")
    record_hash = "0x" + bytes(record[2]).hex()
    if expected_hash and record_hash.lower() != expected_hash.lower():
        raise HTTPException(409, "The file does not match this record on the current blockchain.")
    return record_hash.lower()


def authorize_patient(user: dict, patient: str):
    if user["role"] == "patient" and user["wallet_address"] == patient.lower():
        return
    if user["role"] != "doctor" or user["status"] != "verified":
        raise HTTPException(403, "Only the patient or an approved doctor may read these reports.")
    if not chain_call(user["context"].contract.functions.hasAccess(
            normalize_address(patient), normalize_address(user["wallet_address"]))):
        raise HTTPException(403, "Patient consent is missing, revoked, or expired.")


def find_report(context: ChainContext, patient: str, record_id: int):
    with database() as db:
        row = db.execute("""SELECT * FROM stored_reports
            WHERE chain_scope = ? AND patient_address = ? AND record_id = ? AND state = 'ready'""",
            (context.scope, patient.lower(), record_id)).fetchone()
        if row:
            return dict(row)
        # Read the original application's table without rewriting or deleting it.
        legacy_exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reports'").fetchone()
        if legacy_exists:
            legacy = db.execute("SELECT * FROM reports WHERE patient_address = ? AND record_id = ?",
                                (patient.lower(), record_id)).fetchone()
            if legacy:
                result = dict(legacy)
                result.update(storage_backend="local", cid=None, encrypted_sha256=None, file_size=0)
                return result
    return None


def report_summary(report: dict):
    return {key: report.get(key) for key in ("record_id", "patient_address", "file_hash", "original_filename",
                                            "content_type", "cid", "storage_backend", "created_at", "file_size")}


@app.get("/reports")
def list_reports(patient_address: str | None = None, user: dict = Depends(current_user)):
    patient = normalize_address(patient_address or user["wallet_address"], "patient address")
    authorize_patient(user, patient)
    context = user["context"]
    with database() as db:
        ids = {row[0] for row in db.execute("SELECT record_id FROM stored_reports WHERE chain_scope = ? AND patient_address = ? AND state = 'ready'",
                                          (context.scope, patient.lower()))}
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reports'").fetchone():
            ids.update(row[0] for row in db.execute("SELECT record_id FROM reports WHERE patient_address = ?", (patient.lower(),)))
    result = []
    for record_id in sorted(ids, reverse=True):
        report = find_report(context, patient, record_id)
        try:
            check_record(context, record_id, patient, report["file_hash"])
        except HTTPException as exc:
            if exc.status_code in {403, 404, 409}:
                continue  # Old local chains must not leak into a newly deployed demo.
            raise
        result.append(report_summary(report))
    return {"reports": result}


async def ipfs_add(encrypted_bytes: bytes) -> str:
    try:
        async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
            response = await client.post(ipfs_endpoint("add"),
                params={"pin": "true", "cid-version": "1", "raw-leaves": "true"},
                files={"file": ("report.enc", encrypted_bytes, "application/octet-stream")})
            response.raise_for_status()
            lines = [json.loads(line) for line in response.text.splitlines() if line.strip()]
            cid = lines[-1]["Hash"]
            if not re.fullmatch(r"b[a-z2-7]{20,200}", cid):
                raise ValueError("Invalid CIDv1")
            return cid
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise HTTPException(503, "IPFS upload failed. Open IPFS Desktop or run ipfs daemon, then retry storage for this record.") from exc


async def ipfs_cat(cid: str) -> bytes:
    try:
        async with httpx.AsyncClient(timeout=45, trust_env=False) as client:
            async with client.stream("POST", ipfs_endpoint("cat"), params={"arg": cid}) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_ENCRYPTED_BYTES:
                        raise HTTPException(502, "The encrypted IPFS object exceeds the expected size.")
                return bytes(body)
    except httpx.HTTPError as exc:
        raise HTTPException(503, "Cannot retrieve this encrypted report from IPFS. Keep its node running and the CID pinned.") from exc


@app.post("/reports")
async def upload_report(file: UploadFile = File(...), record_id: int = Form(...),
                        file_hash: str = Form(...), user: dict = Depends(current_user)):
    if user["role"] != "patient":
        raise HTTPException(403, "Only a patient may upload their own medical report.")
    patient = normalize_address(user["wallet_address"])
    context = user["context"]
    file_hash = normalized_hash(file_hash)
    check_record(context, record_id, patient, file_hash)
    file_bytes = await file.read(MAX_FILE_SIZE_BYTES + 1)
    if not file_bytes:
        raise HTTPException(400, "Select a non-empty report.")
    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(413, f"The maximum report size is {MAX_FILE_SIZE_MB} MB.")
    if keccak_hex(file_bytes) != file_hash:
        raise HTTPException(400, "The uploaded bytes do not match the on-chain file hash.")
    existing = find_report(context, patient, record_id)
    if existing and existing["file_hash"].lower() == file_hash:
        raise HTTPException(409, "This record already has a stored file.")
    encrypted_bytes = get_fernet().encrypt(file_bytes)
    filename = Path(PureWindowsPath(file.filename or "medical-report.bin").name).name[:200]
    mime = file.content_type or "application/octet-stream"
    now = int(time.time())
    upload_id = uuid4().hex
    try:
        with database() as db:
            db.execute("DELETE FROM stored_reports WHERE state = 'pending' AND created_at < ?", (now - 600,))
            db.execute("""INSERT INTO stored_reports
                (chain_scope, patient_address, record_id, file_hash, original_filename, content_type,
                 file_size, storage_backend, created_at, state, upload_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'ipfs', ?, 'pending', ?)""",
                (context.scope, patient.lower(), record_id, file_hash, filename, mime, len(file_bytes), now, upload_id))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "This record is already stored or its upload is in progress.") from exc
    try:
        cid = await ipfs_add(encrypted_bytes)
        # Ensure the node was not reset while the IPFS operation was in flight.
        latest = chain_context()
        if latest.scope != context.scope:
            raise HTTPException(409, "The blockchain restarted during upload. Deploy and sign in again.")
        check_record(latest, record_id, patient, file_hash)
        with database() as db:
            db.execute("""UPDATE stored_reports SET cid = ?, encrypted_sha256 = ?, state = 'ready'
                WHERE upload_id = ?""", (cid, hashlib.sha256(encrypted_bytes).hexdigest(), upload_id))
    except Exception:
        with database() as db:
            db.execute("DELETE FROM stored_reports WHERE upload_id = ? AND state = 'pending'", (upload_id,))
        raise
    return {"message": "Report encrypted and pinned to IPFS.", "record_id": record_id,
            "patient_address": patient, "file_hash": file_hash, "cid": cid, "storage_backend": "ipfs"}


def consume_audit_receipt(user: dict, patient: str, record_id: int, tx_hash: str):
    """Require a fresh confirmed access event, not a reusable client-side claim."""
    if not re.fullmatch(r"0x[0-9a-fA-F]{64}", tx_hash):
        raise HTTPException(400, "A confirmed Log Record Access transaction is required for doctor downloads.")
    context = user["context"]
    try:
        receipt = context.web3.eth.get_transaction_receipt(tx_hash)
        transaction = context.web3.eth.get_transaction(tx_hash)
        block = context.web3.eth.get_block(receipt["blockNumber"])
        if (receipt["status"] != 1 or not transaction.get("to")
                or transaction["to"].lower() != context.address.lower()
                or transaction["from"].lower() != user["wallet_address"]
                or abs(int(time.time()) - int(block["timestamp"])) > 600):
            raise ValueError("Wrong transaction or expired receipt")
        matched = False
        for log in receipt["logs"]:
            if log["address"].lower() != context.address.lower():
                continue
            try:
                args = context.contract.events.RecordAccessed().process_log(log)["args"]
                if (args["recordId"] == record_id and args["doctor"].lower() == user["wallet_address"]
                        and args["patient"].lower() == patient.lower()):
                    matched = True
            except Exception:
                continue
        if not matched:
            raise ValueError("No matching access event")
    except Exception as exc:
        raise HTTPException(403, "Log this record access on-chain with your doctor wallet before downloading.") from exc
    try:
        with database() as db:
            db.execute("INSERT INTO audit_receipts VALUES (?, ?, ?, ?, ?)",
                       (tx_hash.lower(), user["id"], record_id, context.scope, int(time.time())))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "That access receipt was already used. Log a new access to download again.") from exc


@app.get("/reports/{record_id}/download")
async def download_report(record_id: int, patient_address: str | None = None,
                          audit_tx_hash: str = "", user: dict = Depends(current_user)):
    patient = normalize_address(patient_address or user["wallet_address"], "patient address")
    authorize_patient(user, patient)
    context = user["context"]
    report = find_report(context, patient, record_id)
    if report is None:
        raise HTTPException(404, "Stored report not found on this blockchain.")
    check_record(context, record_id, patient, report["file_hash"])
    if report["storage_backend"] == "ipfs":
        encrypted = await ipfs_cat(report["cid"])
        if not hmac.compare_digest(hashlib.sha256(encrypted).hexdigest(), report["encrypted_sha256"]):
            raise HTTPException(502, "Encrypted IPFS content failed its integrity check.")
    else:
        filename = report["stored_filename"]
        if filename != Path(filename).name or "\\" in filename:
            raise HTTPException(500, "Invalid legacy storage reference.")
        storage_path = ENCRYPTED_DIR / filename
        if not storage_path.is_file():
            raise HTTPException(404, "The legacy encrypted report file is missing.")
        if storage_path.stat().st_size > MAX_ENCRYPTED_BYTES:
            raise HTTPException(413, "The legacy report exceeds the configured size limit.")
        encrypted = storage_path.read_bytes()
    try:
        decrypted = get_fernet().decrypt(encrypted)
    except InvalidToken as exc:
        raise HTTPException(500, "This report cannot be decrypted. Restore its original FERNET_KEY.") from exc
    if keccak_hex(decrypted) != report["file_hash"]:
        raise HTTPException(502, "The report failed its blockchain hash integrity check.")
    # Recheck consent after network retrieval; a revocation must take effect promptly.
    latest = chain_context()
    if latest.scope != context.scope:
        raise HTTPException(409, "The local blockchain changed during the download. Sign in again.")
    user["context"] = latest
    authorize_patient(user, patient)
    check_record(latest, record_id, patient, report["file_hash"])
    if user["role"] == "doctor":
        consume_audit_receipt(user, patient, record_id, audit_tx_hash)
    filename = Path(PureWindowsPath(report["original_filename"]).name).name
    return Response(decrypted, media_type=report["content_type"], headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

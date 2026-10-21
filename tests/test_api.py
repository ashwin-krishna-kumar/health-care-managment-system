"""Security and storage tests using real wallet signatures and encryption.

The blockchain and IPFS network are isolated doubles; their interfaces and
payloads are checked here. No existing project database or IPFS pins are used.
"""
import base64
import hashlib
import os
import tempfile
import time
from email import policy
from email.parser import BytesParser
from types import SimpleNamespace

_import_data = tempfile.TemporaryDirectory(prefix="healthrecords-import-")
os.environ["HEALTHRECORDS_DATA_DIR"] = _import_data.name

import httpx
import pytest
from cryptography.fernet import Fernet
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient
from web3.exceptions import ContractLogicError
from backend import main as api
from backend.configure_env import configure
from dotenv import dotenv_values

PASSWORD = "a-long-demo-password-2026"
ORIGIN = "http://127.0.0.1:5500"


class Chain:
    def __init__(self):
        self.accounts = [Account.create() for _ in range(5)]
        self.admin = self.accounts[0].address
        self.verified = set()
        self.consent = set()
        self.emergencies = {}
        self.records = {}
        self.receipts = {}
        self.transactions = {}
        self.scope = "31337:test-contract:test-genesis"
        self.address = api.normalize_address("0x" + "a" * 40)
        self.contract = SimpleNamespace(functions=self,
            events=SimpleNamespace(RecordAccessed=lambda: SimpleNamespace(process_log=lambda log: {"args": log["args"]})))
        self.context = api.ChainContext(SimpleNamespace(eth=self), self.contract, self.scope, 31337, self.address)

    def value(self, result):
        return SimpleNamespace(call=lambda: result)

    def admin_call(self):
        return self.value(self.admin)

    def verifiedProviders(self, wallet):
        return self.value(wallet.lower() in self.verified)

    def hasAccess(self, patient, doctor):
        key = (patient.lower(), doctor.lower())
        return self.value(doctor.lower() in self.verified and (
            key in self.consent or self.emergencies.get(key, 0) > time.time()))

    def getMedicalRecord(self, record_id):
        def read():
            if record_id not in self.records:
                raise ContractLogicError("RecordNotFound")
            return self.records[record_id]
        return SimpleNamespace(call=read)

    def add_record(self, patient, content, record_id=1):
        digest = api.keccak_hex(content)
        self.records[record_id] = (record_id, patient.address, bytes.fromhex(digest[2:]), b"\0" * 32, int(time.time()))
        return digest

    def receipt(self, patient, doctor, record_id=1, number=1):
        tx_hash = "0x" + f"{number:064x}"
        self.receipts[tx_hash] = {"status": 1, "blockNumber": 1, "logs": [{"address": self.address,
            "args": {"recordId": record_id, "patient": patient.address, "doctor": doctor.address}}]}
        self.transactions[tx_hash] = {"to": self.address, "from": doctor.address}
        return tx_hash

    def get_transaction_receipt(self, tx_hash):
        return self.receipts[tx_hash]

    def get_transaction(self, tx_hash):
        return self.transactions[tx_hash]

    def get_block(self, number):
        return {"timestamp": int(time.time())}


class IPFS:
    def __init__(self):
        self.objects = {}
        self.add_calls = 0
        self.fail_add = False
        self.fail_cat = False

    def handle(self, request):
        assert request.method == "POST"
        if request.url.path.endswith("/add"):
            if self.fail_add:
                return httpx.Response(503)
            assert request.url.params["pin"] == "true"
            assert request.url.params["cid-version"] == "1"
            envelope = b"Content-Type: " + request.headers["content-type"].encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + request.content
            parts = list(BytesParser(policy=policy.default).parsebytes(envelope).iter_parts())
            assert len(parts) == 1
            assert parts[0].get_filename() == "report.enc"
            content = parts[0].get_payload(decode=True)
            raw_cid = b"\x01\x55\x12\x20" + hashlib.sha256(content).digest()
            cid = "b" + base64.b32encode(raw_cid).decode().lower().rstrip("=")
            self.objects[cid] = content
            self.add_calls += 1
            return httpx.Response(200, json={"Hash": cid, "Name": "report.enc", "Size": str(len(content))})
        if request.url.path.endswith("/cat"):
            if self.fail_cat:
                return httpx.Response(503)
            return httpx.Response(200, content=self.objects[request.url.params["arg"]])
        raise AssertionError(request.url)


@pytest.fixture
def environment(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DATA_DIR", tmp_path)
    monkeypatch.setattr(api, "ENCRYPTED_DIR", tmp_path / "encrypted")
    monkeypatch.setattr(api, "DATABASE_PATH", tmp_path / "reports.db")
    monkeypatch.setattr(api, "FERNET_KEY", Fernet.generate_key().decode())
    api.initialize_database()
    chain = Chain()
    # The functions namespace exposes admin() while the fixture retains its value.
    chain.contract.functions = SimpleNamespace(admin=chain.admin_call,
        verifiedProviders=chain.verifiedProviders, hasAccess=chain.hasAccess,
        getMedicalRecord=chain.getMedicalRecord)
    monkeypatch.setattr(api, "chain_context", lambda: chain.context)
    ipfs = IPFS()
    original_client = httpx.AsyncClient
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda *args, **kwargs: original_client(
        *args, transport=httpx.MockTransport(ipfs.handle), **kwargs))
    with TestClient(api.app, headers={"Origin": ORIGIN}) as client:
        yield client, chain, ipfs


def credentials(client, chain, account, role, action="register", email=None, password=PASSWORD):
    email = email or f"{role}-{account.address.lower()}@example.test"
    challenge = client.post("/auth/challenge", json={"wallet_address": account.address,
        "email": email, "role": role, "action": action, "contract_address": chain.address})
    assert challenge.status_code == 200, challenge.text
    body = challenge.json()
    return {"challenge_id": body["challenge_id"], "signature": Account.sign_message(
        encode_defunct(text=body["message"]), account.key).signature.hex(), "email": email,
        "password": password, "wallet_address": account.address, "role": role, "full_name": f"Demo {role}"}


def register(client, chain, account, role):
    response = client.post("/auth/register", json=credentials(client, chain, account, role))
    assert response.status_code == 200, response.text
    return response


def login(client, chain, account, role):
    response = client.post("/auth/login", json=credentials(client, chain, account, role, action="login"))
    assert response.status_code == 200, response.text
    return response


def upload(client, chain, content, record_id=1, digest=None):
    digest = digest or chain.add_record(chain.accounts[1], content, record_id)
    return client.post("/reports", files={"file": ("blood-test.pdf", content, "application/pdf")},
                       data={"record_id": record_id, "file_hash": digest})


def test_registration_password_storage_session_and_logout(environment):
    client, chain, _ = environment
    assert client.get("/reports").status_code == 401
    response = register(client, chain, chain.accounts[1], "patient")
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    assert response.headers["access-control-allow-credentials"] == "true"
    assert response.json()["user"]["role"] == "patient"
    assert "password_hash" not in response.text
    with api.database() as db:
        stored = db.execute("SELECT password_hash FROM users").fetchone()[0]
        token_hash = db.execute("SELECT token_hash FROM sessions").fetchone()[0]
    assert PASSWORD not in stored and stored.startswith("pbkdf2_sha256$600000$")
    assert token_hash != client.cookies.get(api.COOKIE_NAME)
    assert client.get("/auth/me").status_code == 200
    assert client.get("/admin/doctors").status_code == 403
    assert client.post("/auth/logout").status_code == 200
    assert client.get("/auth/me").status_code == 401


def test_login_requires_password_wallet_and_matching_role(environment):
    client, chain, _ = environment
    account = chain.accounts[1]
    register(client, chain, account, "patient")
    client.post("/auth/logout")
    payload = credentials(client, chain, account, "patient", "login", password="wrong-password")
    assert client.post("/auth/login", json=payload).status_code == 401
    assert client.post("/auth/login", json=payload).status_code == 401  # one-use nonce
    payload = credentials(client, chain, account, "admin", "login",
                          email=f"patient-{account.address.lower()}@example.test")
    assert client.post("/auth/login", json=payload).status_code == 401
    assert client.get("/auth/me").status_code == 401
    assert login(client, chain, account, "patient").status_code == 200


def test_forged_signature_and_expired_challenge_are_rejected(environment):
    client, chain, _ = environment
    payload = credentials(client, chain, chain.accounts[1], "patient")
    payload["signature"] = Account.sign_message(encode_defunct(text="different message"), chain.accounts[2].key).signature.hex()
    assert client.post("/auth/register", json=payload).status_code == 401
    payload = credentials(client, chain, chain.accounts[1], "patient")
    with api.database() as db:
        db.execute("UPDATE auth_challenges SET expires_at = 0")
    assert client.post("/auth/register", json=payload).status_code == 401


def test_admin_requires_actual_contract_owner_and_doctors_need_approval(environment):
    client, chain, _ = environment
    forged = credentials(client, chain, chain.accounts[1], "admin")
    assert client.post("/auth/register", json=forged).status_code == 403
    doctor = register(client, chain, chain.accounts[2], "doctor")
    assert doctor.json()["user"]["status"] == "pending"
    assert client.get("/reports", params={"patient_address": chain.accounts[1].address}).status_code == 403
    register(client, chain, chain.accounts[0], "admin")
    assert client.get("/admin/doctors").json()["doctors"][0]["verified"] is False
    chain.verified.add(chain.accounts[2].address.lower())
    assert client.get("/admin/doctors").json()["doctors"][0]["verified"] is True
    chain.admin = chain.accounts[4].address
    assert client.get("/admin/doctors").status_code == 403


def test_ipfs_receives_only_encrypted_bytes_and_patient_can_download(environment):
    client, chain, ipfs = environment
    register(client, chain, chain.accounts[1], "patient")
    original = b"Synthetic medical report for encryption integration test."
    response = upload(client, chain, original)
    assert response.status_code == 200, response.text
    cid = response.json()["cid"]
    encrypted = ipfs.objects[cid]
    assert original not in encrypted
    assert api.get_fernet().decrypt(encrypted) == original
    reports = client.get("/reports").json()["reports"]
    assert reports[0]["cid"] == cid and reports[0]["storage_backend"] == "ipfs"
    result = client.get("/reports/1/download")
    assert result.status_code == 200 and result.content == original
    assert result.headers["cache-control"] == "no-store"
    assert "blood-test.pdf" in result.headers["content-disposition"]
    assert not list(api.ENCRYPTED_DIR.iterdir())  # new bytes are not pretending to be IPFS locally


def test_upload_checks_onchain_ownership_record_and_file_hash(environment):
    client, chain, ipfs = environment
    register(client, chain, chain.accounts[1], "patient")
    wrong_owner = chain.add_record(chain.accounts[3], b"test")
    assert upload(client, chain, b"test", digest=wrong_owner).status_code == 403
    correct_hash = chain.add_record(chain.accounts[1], b"correct")
    assert upload(client, chain, b"different", digest=correct_hash).status_code == 400
    assert upload(client, chain, b"correct", digest="0x" + "0" * 64).status_code == 409
    assert ipfs.add_calls == 0


def test_ipfs_outage_leaves_retry_possible_and_duplicates_are_blocked(environment):
    client, chain, ipfs = environment
    register(client, chain, chain.accounts[1], "patient")
    original = b"Synthetic retry test"
    ipfs.fail_add = True
    assert upload(client, chain, original).status_code == 503
    with api.database() as db:
        assert db.execute("SELECT COUNT(*) FROM stored_reports").fetchone()[0] == 0
    ipfs.fail_add = False
    assert upload(client, chain, original).status_code == 200
    assert upload(client, chain, original).status_code == 409
    assert ipfs.add_calls == 1


def test_doctor_needs_consent_and_a_fresh_matching_audit_transaction(environment):
    client, chain, _ = environment
    patient, doctor = chain.accounts[1:3]
    register(client, chain, patient, "patient")
    assert upload(client, chain, b"Synthetic consent report").status_code == 200
    chain.verified.add(doctor.address.lower())
    register(client, chain, doctor, "doctor")
    params = {"patient_address": patient.address}
    assert client.get("/reports", params=params).status_code == 403
    chain.consent.add((patient.address.lower(), doctor.address.lower()))
    assert client.get("/reports", params=params).status_code == 200
    assert client.get("/reports/1/download", params=params).status_code == 400
    params["audit_tx_hash"] = chain.receipt(patient, doctor)
    assert client.get("/reports/1/download", params=params).content == b"Synthetic consent report"
    assert client.get("/reports/1/download", params=params).status_code == 409
    chain.consent.clear()
    params["audit_tx_hash"] = chain.receipt(patient, doctor, number=2)
    assert client.get("/reports/1/download", params=params).status_code == 403


def test_expired_emergency_and_removed_doctor_cannot_read(environment):
    client, chain, _ = environment
    patient, doctor = chain.accounts[1:3]
    register(client, chain, patient, "patient")
    assert upload(client, chain, b"Synthetic emergency report").status_code == 200
    chain.verified.add(doctor.address.lower())
    register(client, chain, doctor, "doctor")
    key = (patient.address.lower(), doctor.address.lower())
    chain.emergencies[key] = time.time() + 86400
    assert client.get("/reports", params={"patient_address": patient.address}).status_code == 200
    chain.emergencies[key] = time.time() - 1
    assert client.get("/reports", params={"patient_address": patient.address}).status_code == 403
    chain.consent.add(key)
    chain.verified.clear()
    assert client.get("/reports", params={"patient_address": patient.address}).status_code == 403


def test_corrupted_ipfs_payload_is_never_released(environment):
    client, chain, ipfs = environment
    register(client, chain, chain.accounts[1], "patient")
    response = upload(client, chain, b"Synthetic integrity report")
    ipfs.objects[response.json()["cid"]] = b"modified encrypted bytes"
    assert client.get("/reports/1/download").status_code == 502


def test_original_local_reports_remain_readable(environment):
    client, chain, ipfs = environment
    patient = chain.accounts[1]
    register(client, chain, patient, "patient")
    original = b"Synthetic original local report"
    digest = chain.add_record(patient, original)
    (api.ENCRYPTED_DIR / "old.enc").write_bytes(api.get_fernet().encrypt(original))
    with api.database() as db:
        db.execute("CREATE TABLE reports (patient_address TEXT, record_id INTEGER, file_hash TEXT, original_filename TEXT, content_type TEXT, stored_filename TEXT, created_at INTEGER)")
        db.execute("INSERT INTO reports VALUES (?, 1, ?, 'old.pdf', 'application/pdf', 'old.enc', ?)",
                   (patient.address.lower(), digest, int(time.time())))
    assert client.get("/reports").json()["reports"][0]["storage_backend"] == "local"
    assert client.get("/reports/1/download").content == original
    assert ipfs.add_calls == 0


def test_sessions_expire_and_chain_reset_requires_new_login(environment):
    client, chain, _ = environment
    patient = chain.accounts[1]
    register(client, chain, patient, "patient")
    chain.context.scope = "31337:test-contract:new-genesis"
    assert client.get("/auth/me").status_code == 401
    login(client, chain, patient, "patient")
    assert client.get("/auth/me").status_code == 200
    with api.database() as db:
        db.execute("UPDATE sessions SET expires_at = 0")
    assert client.get("/auth/me").status_code == 401


def test_untrusted_origins_and_contract_mismatch_are_rejected(environment):
    client, chain, _ = environment
    payload = {"wallet_address": chain.accounts[1].address, "email": "patient@example.test",
               "role": "patient", "action": "register", "contract_address": chain.address}
    assert client.post("/auth/challenge", json=payload, headers={"Origin": "https://other.example"}).status_code == 403
    payload["contract_address"] = "0x" + "b" * 40
    assert client.post("/auth/challenge", json=payload).status_code == 409


def test_registration_password_minimum_and_login_attempt_limit(environment):
    client, chain, _ = environment
    patient = chain.accounts[1]
    payload = credentials(client, chain, patient, "patient", password="short")
    assert client.post("/auth/register", json=payload).status_code == 400
    register(client, chain, patient, "patient")
    for _ in range(10):
        payload = credentials(client, chain, patient, "patient", "login", password="incorrect")
        assert client.post("/auth/login", json=payload).status_code == 401
    payload = credentials(client, chain, patient, "patient", "login")
    assert client.post("/auth/login", json=payload).status_code == 429


def test_setup_preserves_existing_encryption_key(tmp_path):
    key = Fernet.generate_key().decode()
    env_path = tmp_path / ".env"
    env_path.write_text(f"FERNET_KEY={key}\nCONTRACT_ADDRESS=0xexisting\n")
    configure(env_path)
    values = dotenv_values(env_path)
    assert values["FERNET_KEY"] == key
    assert values["CONTRACT_ADDRESS"] == "0xexisting"
    assert values["IPFS_API_URL"] == "http://127.0.0.1:5001/api/v0"


def test_setup_stops_if_original_key_is_missing_for_existing_files(tmp_path):
    encrypted = tmp_path / "data" / "encrypted"
    encrypted.mkdir(parents=True)
    (encrypted / "existing.enc").write_bytes(b"placeholder-existing-report")
    with pytest.raises(SystemExit, match="Restore the original key"):
        configure(tmp_path / ".env")
    assert not dotenv_values(tmp_path / ".env").get("FERNET_KEY")

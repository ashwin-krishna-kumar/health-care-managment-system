"""Configure and verify IPFS from the existing HealthRecords project root.

Run: python scripts/connect_ipfs.py
Optional: --api-url http://127.0.0.1:5001 --contract-address 0x...
Only a random, encrypted test object is uploaded; no patient files are used.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import sys
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

import httpx
from cryptography.fernet import Fernet
from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parents[1]


def has_saved_reports(backend: Path, values: dict) -> bool:
    data = Path(values.get("HEALTHRECORDS_DATA_DIR") or backend / "data")
    if not data.is_absolute():
        data = ROOT / data
    encrypted = data / "encrypted"
    if encrypted.is_dir() and any(encrypted.iterdir()):
        return True
    database = data / "reports.db"
    if not database.exists():
        return False
    try:
        with sqlite3.connect(database) as db:
            for table in ("reports", "stored_reports"):
                exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
                if exists and db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                    return True
    except sqlite3.Error:
        return True  # Do not create a replacement key when old data cannot be inspected.
    return False


def configure(backend: Path, api_url: str | None, contract_address: str | None) -> Path:
    env_path = backend / ".env"
    values = dict(dotenv_values(env_path)) if env_path.exists() else {}
    # Backend environment variables take precedence over .env values too.
    effective = {**values, **os.environ}
    url = (api_url or effective.get("IPFS_API_URL") or "http://127.0.0.1:5001/api/v0").strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc or parts.query or parts.fragment:
        raise ValueError("Use an HTTP URL such as http://127.0.0.1:5001/api/v0.")
    path = parts.path.rstrip("/") or "/api/v0"
    if not path.endswith("/api/v0"):
        raise ValueError("Use the RPC base /api/v0, not /webui or /ipfs.")
    updates = {"IPFS_API_URL": urlunsplit((parts.scheme, parts.netloc, path, "", ""))}
    if contract_address:
        if not re.fullmatch(r"0x[0-9a-fA-F]{40}", contract_address):
            raise ValueError("The contract address must be 0x followed by 40 hexadecimal characters.")
        updates["CONTRACT_ADDRESS"] = contract_address
    key = (effective.get("FERNET_KEY") or "").strip()
    if key:
        try:
            Fernet(key.encode("ascii"))
        except (ValueError, TypeError, UnicodeError) as exc:
            raise ValueError("FERNET_KEY is invalid. Restore the original encryption key in backend/.env.") from exc
    else:
        if has_saved_reports(backend, effective):
            raise ValueError("Existing reports were found without FERNET_KEY. Restore the original key in backend/.env; a new key cannot read them.")
        updates["FERNET_KEY"] = Fernet.generate_key().decode("ascii")
    for name, default in {
        "RPC_URL": "http://127.0.0.1:8545",
        "EXPECTED_CHAIN_ID": "31337",
        "FRONTEND_ORIGINS": "http://127.0.0.1:5500,http://localhost:5500",
    }.items():
        if not effective.get(name):
            updates[name] = default
    if env_path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        shutil.copy2(env_path, env_path.with_name(".env.before-ipfs-" + stamp))
    for name, value in updates.items():
        set_key(str(env_path), name, value)
    # Make the just-written explicit settings effective for this check too.
    for name, value in updates.items():
        os.environ[name] = value
    return env_path


def load_backend(backend: Path):
    spec = importlib.util.spec_from_file_location("healthrecords_ipfs_check", backend / "main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def verify_storage(module) -> str:
    cipher = Fernet(Fernet.generate_key())
    sample = b"HealthRecords IPFS connection test: " + secrets.token_bytes(32)
    encrypted = cipher.encrypt(sample)
    cid = await module.ipfs_add(encrypted)
    try:
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            response = await client.post(module.ipfs_endpoint("pin/ls"), params={"arg": cid, "type": "all"})
            response.raise_for_status()
            if cid not in response.json().get("Keys", {}):
                raise ValueError("The test file was added but its pin was not found.")
        downloaded = await module.ipfs_cat(cid)
        if downloaded != encrypted or cipher.decrypt(downloaded) != sample:
            raise ValueError("The IPFS upload and download did not match.")
    finally:
        try:
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                response = await client.post(module.ipfs_endpoint("pin/rm"), params={"arg": cid})
                response.raise_for_status()
        except (httpx.HTTPError, ValueError):
            print("NOTE: The temporary test object remains pinned; your reports were not changed.")
    return cid


def main() -> int:
    parser = argparse.ArgumentParser(description="Connect the existing HealthRecords backend to a local IPFS node.")
    parser.add_argument("--api-url", help="Kubo RPC base, normally http://127.0.0.1:5001/api/v0")
    parser.add_argument("--contract-address", help="Optional existing deployed HealthRecords contract address")
    args = parser.parse_args()
    os.chdir(ROOT)
    backend = ROOT / "backend"
    if not (backend / "main.py").is_file():
        print("Copy this update into your existing project so backend/main.py and scripts/connect_ipfs.py are present.")
        return 1
    try:
        configure(backend, args.api_url, args.contract_address)
        print("Configured backend/.env. Existing settings and encryption keys were preserved.")
        module = load_backend(backend)
        status = module.ipfs_connection_status()
        if not status["connected"]:
            print("IPFS is not connected: " + status["error"])
            print("Open IPFS Desktop, wait for it to start, then run this command again.")
            print("Install: https://docs.ipfs.tech/install/ipfs-desktop/")
            return 1
        print("Kubo node connected (version " + status["version"] + ").")
        cid = asyncio.run(verify_storage(module))
        print("PASS: encrypted upload, pin check, download and decryption.")
        print("Test CID: " + cid + " (temporary test pin removed unless noted above).")
        health = module.health()
        if not health["encryption_configured"]:
            print("NEXT: Restore the valid FERNET_KEY in backend/.env.")
            return 1
        if not health["contract_configured"]:
            print("NEXT: Start your Hardhat node and set CONTRACT_ADDRESS in backend/.env to the deployed contract.")
            print("IPFS passed; the complete application still needs its blockchain connection.")
        print("Restart the backend from the project root:")
        print("  python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000")
        print("Check http://127.0.0.1:8000/health for ipfs_connected: true.")
        return 0
    except Exception as exc:
        print("Connection check failed: " + str(getattr(exc, "detail", None) or exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

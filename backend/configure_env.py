"""Add safe local defaults without replacing an existing encryption key."""
from pathlib import Path
import argparse
import sqlite3

from cryptography.fernet import Fernet
from dotenv import dotenv_values, set_key


def configure(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()
        path.chmod(0o600)
    values = dotenv_values(path)
    key = (values.get("FERNET_KEY") or "").strip()
    if not key:
        encrypted_dir = path.parent / "data" / "encrypted"
        existing_reports = any(encrypted_dir.glob("*.enc"))
        db_path = path.parent / "data" / "reports.db"
        if db_path.exists():
            with sqlite3.connect(db_path) as db:
                for table in ("reports", "stored_reports"):
                    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                        existing_reports = existing_reports or db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] > 0
        if existing_reports:
            raise SystemExit("Existing reports were found but FERNET_KEY is missing. Restore the original key before continuing.")
        set_key(str(path), "FERNET_KEY", Fernet.generate_key().decode())
        print("Created an encryption key for this new, empty report store.")
    else:
        try:
            Fernet(key.encode("ascii"))
        except (ValueError, UnicodeError):
            raise SystemExit("FERNET_KEY is invalid. Restore the original key; it has not been replaced.")
        print("Existing encryption key preserved.")
    defaults = {"RPC_URL": "http://127.0.0.1:8545", "IPFS_API_URL": "http://127.0.0.1:5001/api/v0",
                "FRONTEND_ORIGINS": "http://127.0.0.1:5500,http://localhost:5500", "MAX_FILE_SIZE_MB": "10",
                "SESSION_SECONDS": "3600", "COOKIE_SECURE": "false", "EXPECTED_CHAIN_ID": "31337"}
    for name, value in defaults.items():
        if not values.get(name):
            set_key(str(path), name, value)
    if not values.get("CONTRACT_ADDRESS"):
        set_key(str(path), "CONTRACT_ADDRESS", "")
        print("The project launcher will select the deployed local contract automatically.")
    print("Local settings are ready. The project launcher starts IPFS and the blockchain.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(__file__).with_name(".env"))
    configure(parser.parse_args().env_file)

"""Test the configured Kubo connection using only a synthetic encrypted file."""
from pathlib import Path
import argparse
import json
import re
import secrets
import sys

import httpx
from cryptography.fernet import Fernet
from dotenv import dotenv_values


def check(api_url: str, client: httpx.Client) -> str:
    api_url = api_url.rstrip("/")
    version = client.post(f"{api_url}/version")
    version.raise_for_status()
    if not version.json().get("Version"):
        raise ValueError("The configured address did not return a Kubo version.")
    # A temporary key ensures this test never uses or changes patient keys.
    cipher = Fernet(Fernet.generate_key())
    original = b"HealthRecords IPFS connection test: " + secrets.token_bytes(32)
    encrypted = cipher.encrypt(original)
    cid = None
    try:
        added = client.post(f"{api_url}/add",
                            params={"pin": "true", "cid-version": "1", "raw-leaves": "true"},
                            files={"file": ("connection-test.enc", encrypted, "application/octet-stream")})
        added.raise_for_status()
        rows = [json.loads(line) for line in added.text.splitlines() if line.strip()]
        candidate = rows[-1]["Hash"]
        if not isinstance(candidate, str) or not re.fullmatch(r"b[a-z2-7]{20,200}", candidate):
            raise ValueError("IPFS returned an unexpected CID.")
        cid = candidate
        with client.stream("POST", f"{api_url}/cat", params={"arg": cid}) as response:
            response.raise_for_status()
            recovered = bytearray()
            for chunk in response.iter_bytes():
                recovered.extend(chunk)
                if len(recovered) > len(encrypted):
                    raise ValueError("IPFS returned an unexpected file size.")
        if bytes(recovered) != encrypted or cipher.decrypt(bytes(recovered)) != original:
            raise ValueError("The IPFS upload/download integrity check failed.")
        print("PASS: connected to Kubo " + version.json()["Version"])
        print("PASS: encrypted upload, CIDv1 retrieval, and exact file recovery.")
        return cid
    finally:
        # Remove only this randomly generated test file's pin, including on failure.
        if cid:
            try:
                result = client.post(f"{api_url}/pin/rm", params={"arg": cid})
                result.raise_for_status()
            except httpx.HTTPError:
                print("Test pin cleanup failed; you may unpin this test CID: " + cid, file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(__file__).with_name(".env"))
    args = parser.parse_args()
    config = dotenv_values(args.env_file)
    url = config.get("IPFS_API_URL") or "http://127.0.0.1:5001/api/v0"
    try:
        with httpx.Client(timeout=30, trust_env=False) as client:
            check(url, client)
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        print("FAIL: " + str(exc), file=sys.stderr)
        print("Open IPFS Desktop (or run ipfs daemon), then retry. Check IPFS_API_URL in backend/.env.", file=sys.stderr)
        return 1
    print("IPFS storage is ready. Restart your backend to load any changed settings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

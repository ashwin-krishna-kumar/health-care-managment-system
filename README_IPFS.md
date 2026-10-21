# Encrypted IPFS storage

`npm run setup` installs Kubo from the official release with SHA-512 verification. `npm run dev` starts the managed node with its RPC API on loopback port 5001. If a valid local Kubo node already occupies that port, it is reused.

The managed node stores data in `.local/ipfs/` and runs offline. The backend encrypts each report with the existing Fernet key before pinning it; it stores the CID and ciphertext digest in SQLite. Downloads recheck permission, verify the ciphertext digest, decrypt, then compare the plaintext hash with the on-chain record.

While the app is running, check storage with:

```sh
.venv/bin/python backend/check_ipfs.py
```

On Windows use `.venv\Scripts\python.exe` instead. The check uses a temporary encryption key, uploads only a synthetic encrypted object, verifies exact retrieval, and removes its test pin. Do not replace the original `backend/.env` encryption key when reports already exist.

For an existing IPFS installation, set `HEALTHRECORDS_IPFS_BIN` to its executable before setup/start. This selects the executable, not a remote storage service. See `README.md` for data backup and the development chain's lifetime.

# Validation — 21 October 2026

Validation was performed in this Linux cloud workspace using Python 3.12.14, Node.js 24.19.0, Chromium 154.0.8037.57, Kubo 0.40.0, and Solidity 0.8.36.

| Check | Actual result |
| --- | --- |
| `npm run setup` | Passed; repeated successfully and preserved the existing encryption key |
| `npm run build` | Passed; pinned local solc compiled the original HealthRecords contract |
| `npm test` | **28 passed**, 1 upstream Starlette/httpx deprecation warning |
| `npm run test:browser` | **3 passed**, including the complete three-role workflow |
| `python scripts/dev.py --check --no-browser` | Passed; real node, deployed contract, API, encryption and IPFS checks succeeded |
| `.venv/bin/python -m pip check` | No broken requirements |
| Startup/browser-runner cleanup | Ports 5500, 8545 and 5001 released after services started by checks were stopped |

The browser workflow created separate administrator, doctor and patient accounts, verified the doctor, uploaded a synthetic report through the real contract/API/Kubo path, confirmed IPFS ciphertext did not contain plaintext, and compared actual downloaded bytes. It rejected doctor access before consent, declined an incoming request, approved a subsequent request, logged a real blockchain access transaction, downloaded as the doctor, revoked permission and confirmed HTTP 403 afterward. It also checked reload/session restoration and logout.

The other browser checks covered a missing extension on a mobile viewport, canceled wallet requests and successful retries, EIP-6963 discovery with another injected extension present, and automatic network addition/switching. The tests use wallet-compatible providers that sign actual local messages and transactions. They do not control the MetaMask extension UI or approve requests in the user's browser.

API tests exercise signatures, challenges, password storage, administrator ownership, doctor verification, consent and emergency expiry, encrypted IPFS payloads, hash/ownership verification, duplicate uploads and retry, corrupted ciphertext, legacy downloads, chain reset/session expiry, untrusted origins, limits, and encryption-key preservation. New site tests check same-origin assets/API and block unlocked-account/admin RPC methods, invalid bodies, oversized requests and actual non-development node chains.

The uploaded Solidity contract and vendored ethers library were compared with the original archive and remain byte-for-byte unchanged. Dependency/artifact TLS, checksum and package-integrity verification were retained. The Kubo release archive was validated against the official SHA-512 checksum before installation.

The Windows wrapper and Windows/macOS Kubo installation paths were not executed on this Linux machine. Real MetaMask approval remains a user action. No public website, mainnet transaction or production deployment was performed. The ephemeral local-chain limitation and data lifetime are explained in README.md.

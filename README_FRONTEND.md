# HealthRecords website

Start the complete app with `npm run dev` after `npm run setup`. The website and API share port 5500. A separate static file server is no longer required.

The portal retains the uploaded responsive design, role-specific login URLs (`?role=patient`, `?role=doctor`, `?role=admin`), wallet dialog, patient/doctor/admin dashboards and blockchain audit trail.

MetaMask is detected through EIP-6963 announcements or its injected browser provider, including when another extension owns `window.ethereum`. The wallet panel handles installation, user cancellation, network addition/switching and connected-account state. Requests for signatures and transactions always use the user's wallet.

Patient requests and active permissions have an interactive list with Approve, Decline, Revoke and emergency Flag for review actions. The service banner reflects real backend, contract, encryption and IPFS checks.

Full setup, wallet connection and validation instructions are in `README.md`.

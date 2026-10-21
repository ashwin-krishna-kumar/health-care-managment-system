# Frontend validation

The current browser tests are in `tests/browser/site.spec.cjs`. All three passed in Chromium on 21 October 2026. See `VALIDATION.md` for the executed workflow and limitations.

The app retains the uploaded responsive interface and adds same-origin service requests, extension discovery through EIP-6963, live service readiness, and actionable incoming/active consent lists. Mobile layout, missing/canceled wallet requests, real local report upload/download, consent rejection/approval/revocation, session restoration and logout were exercised. Actual MetaMask extension popups require the user’s approval.

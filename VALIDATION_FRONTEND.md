# Frontend validation

The current browser tests are in `tests/browser/site.spec.cjs`. All three passed in Chromium on 21 October 2026. See `VALIDATION.md` for the executed workflow and limitations.

For the green 3.1.0 update, the running website was additionally inspected in Chromium at desktop (1440 px) and mobile (390 px) widths. All three login roles resolved the green primary button and dark green hero/sidebar colours. The wallet dialog opened and displayed the missing-extension action correctly. The mobile page had no horizontal overflow, and no page errors were recorded. The actual desktop screenshot is saved as `docs/green-login.png`. This update changes presentation and versions the asset URLs; the earlier functional suite results describe the underlying app workflow.

The app retains the uploaded responsive interface and adds same-origin service requests, extension discovery through EIP-6963, live service readiness, and actionable incoming/active consent lists. Mobile layout, missing/canceled wallet requests, real local report upload/download, consent rejection/approval/revocation, session restoration and logout were exercised. Actual MetaMask extension popups require the user’s approval.

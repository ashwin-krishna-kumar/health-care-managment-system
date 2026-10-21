const { test, expect } = require("@playwright/test");
const { JsonRpcProvider, HDNodeWallet, NonceManager, getBytes } = require("ethers");
const fs = require("node:fs/promises");
const crypto = require("node:crypto");

// These are Hardhat's PUBLIC development accounts. Never use them on a real network.
const PUBLIC_TEST_MNEMONIC = "test test test test test test test test test test test junk";
const PASSWORD = "healthrecords-browser-test-2026";

async function walletPage(browser, index, options = {}) {
  const context = await browser.newContext();
  const rpc = new JsonRpcProvider("http://127.0.0.1:5500/rpc", undefined, { cacheTimeout: -1, pollingInterval: 50 });
  const wallet = HDNodeWallet.fromPhrase(PUBLIC_TEST_MNEMONIC, undefined, `m/44'/60'/0'/0/${index}`).connect(rpc);
  const signer = new NonceManager(wallet);
  let connected = false;
  let chain = options.wrongNetwork ? "0x1" : "0x7a69";
  let networkAdded = !options.wrongNetwork;
  let reject = options.rejectOnce;
  const methods = [];
  await context.exposeFunction("healthRecordsWalletRequest", async ({ method, params = [] }) => {
    methods.push(method);
    try {
      if (method === "eth_requestAccounts") {
        if (reject) { reject = false; return { error: { code: 4001, message: "User rejected request" } }; }
        connected = true; return { result: [wallet.address] };
      }
      if (method === "eth_accounts") return { result: connected ? [wallet.address] : [] };
      if (method === "eth_chainId") return { result: chain };
      if (method === "wallet_switchEthereumChain") {
        if (!networkAdded) return { error: { code: 4902, message: "Unknown chain" } };
        chain = params[0].chainId; return { result: null };
      }
      if (method === "wallet_addEthereumChain") {
        if (params[0].rpcUrls[0] !== "http://127.0.0.1:5500/rpc") throw new Error("Incorrect wallet RPC address");
        networkAdded = true; chain = params[0].chainId; return { result: null };
      }
      if (method === "personal_sign") {
        if (params[1].toLowerCase() !== wallet.address.toLowerCase()) throw new Error("Wrong signing account");
        return { result: await wallet.signMessage(getBytes(params[0])) };
      }
      if (method === "eth_sendTransaction") {
        const { from, gas, ...transaction } = params[0];
        if (from.toLowerCase() !== wallet.address.toLowerCase()) throw new Error("Wrong transaction account");
        if (gas) transaction.gasLimit = gas;
        return { result: (await signer.sendTransaction(transaction)).hash };
      }
      return { result: await rpc.send(method, params) };
    } catch (error) {
      return { error: { code: typeof error.code === "number" ? error.code : -32603, message: error.message } };
    }
  });
  await context.addInitScript(({ eipOnly }) => {
    const listeners = new Map();
    const provider = {
      isMetaMask: true,
      on: (event, callback) => { if (!listeners.has(event)) listeners.set(event, []); listeners.get(event).push(callback); },
      request: async (request) => {
        const response = await window.healthRecordsWalletRequest(request);
        if (response.error) throw Object.assign(new Error(response.error.message), response.error);
        if (["wallet_switchEthereumChain", "wallet_addEthereumChain"].includes(request.method)) {
          for (const callback of listeners.get("chainChanged") || []) callback(request.params[0].chainId);
        }
        return response.result;
      },
    };
    // EIP-6963 must work even with another extension in window.ethereum.
    window.ethereum = eipOnly ? { isMetaMask: false } : provider;
    const announce = () => window.dispatchEvent(new CustomEvent("eip6963:announceProvider", { detail: {
      info: { uuid: "healthrecords-test-metamask", name: "MetaMask", rdns: "io.metamask", icon: "" }, provider,
    } }));
    window.addEventListener("eip6963:requestProvider", announce);
  }, { eipOnly: Boolean(options.eipOnly) });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  return { page, context, wallet, rpc, methods, errors, close: async () => { await context.close(); rpc.destroy(); } };
}

async function register(app, role, suffix) {
  await app.page.goto("/?role=" + role);
  await expect(app.page.locator("#serviceSummary")).toContainText("All services connected");
  await app.page.locator("#toggleAuthMode").click();
  await app.page.locator("#fullName").fill("Browser " + role);
  await app.page.locator("#email").fill(`${role}-${suffix}@example.test`);
  await app.page.locator("#password").fill(PASSWORD);
  await app.page.locator("#authSubmitButton").click();
  await expect(app.page.locator("#dashboardView")).toBeVisible();
  await expect(app.page.locator("#identityRole")).toContainText(role === "admin" ? "Administrator" : role === "patient" ? "Patient" : "Doctor");
}

test("missing MetaMask is explained and the login fits a mobile screen", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?role=doctor");
  await expect(page.locator("#authTitle")).toHaveText("Doctor sign in");
  await expect(page.locator("#serviceSummary")).toContainText("All services connected");
  await page.locator("#openWalletDialog").click();
  await expect(page.locator("#walletInstallLink")).toBeVisible();
  await expect(page.locator("#walletModalConnect")).toBeDisabled();
  await expect(page.locator("#walletModalStatus")).toContainText("Install MetaMask");
  await page.locator("#closeWalletDialog").click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("MetaMask cancellation can be retried and the local network is added", async ({ browser }) => {
  const app = await walletPage(browser, 4, { wrongNetwork: true, rejectOnce: true, eipOnly: true });
  try {
    await app.page.goto("/");
    await app.page.locator("#openWalletDialog").click();
    await app.page.locator("#walletModalConnect").click();
    await expect(app.page.locator("#walletModalError")).toContainText("cancelled");
    await app.page.locator("#walletModalConnect").click();
    await expect(app.page.locator("#walletModalContinue")).toBeVisible();
    await expect(app.page.locator("#walletModalAddress")).toHaveText(app.wallet.address);
    expect(app.methods).toContain("wallet_addEthereumChain");
    expect(app.errors).toEqual([]);
  } finally { await app.close(); }
});

test("patient, admin, and doctor complete an encrypted consent-controlled report workflow", async ({ browser }) => {
  const suffix = crypto.randomUUID();
  const admin = await walletPage(browser, 0);
  const patient = await walletPage(browser, 1, { eipOnly: true });
  const doctor = await walletPage(browser, 2);
  const applications = [admin, patient, doctor];
  try {
    await register(admin, "admin", suffix);
    await register(doctor, "doctor", suffix);
    await expect(doctor.page.locator("#doctorPendingNotice")).toBeVisible();
    await expect(doctor.page.locator("#requestAccessButton")).toBeDisabled();
    await admin.page.locator("#refreshDoctorsButton").click();
    await admin.page.locator("#doctorsList").getByRole("button", { name: "Verify", exact: true }).click();
    await expect(admin.page.locator("#doctorsList")).toContainText("Verified");
    await doctor.page.locator("#refreshAuditButton").click();
    await expect(doctor.page.locator("#roleStatusBadge")).toHaveText("Verified doctor");

    await register(patient, "patient", suffix);
    const content = Buffer.from("Synthetic medical report for automated testing only. " + suffix);
    await patient.page.locator("#medicalFile").setInputFiles({ name: "synthetic-report.txt", mimeType: "text/plain", buffer: content });
    await patient.page.locator("#recordMetadata").fill("Synthetic checkup");
    await patient.page.locator("#anchorRecordButton").click();
    await expect(patient.page.locator("#patientReportsList")).toContainText("synthetic-report.txt");
    const reportResponse = await patient.page.request.get("/api/reports");
    expect(reportResponse.ok()).toBe(true);
    const report = (await reportResponse.json()).reports[0];
    expect(report.storage_backend).toBe("ipfs");
    const ciphertext = Buffer.from(await (await fetch("http://127.0.0.1:5001/api/v0/cat?arg=" + report.cid, { method: "POST" })).arrayBuffer());
    expect(ciphertext.includes(content)).toBe(false);

    const ownDownloadEvent = patient.page.waitForEvent("download");
    await patient.page.locator("#patientReportsList").getByRole("button", { name: "Download", exact: true }).click();
    const ownDownload = await ownDownloadEvent;
    expect(await fs.readFile(await ownDownload.path())).toEqual(content);

    await doctor.page.locator("#accessPatientAddress").fill(patient.wallet.address);
    await doctor.page.locator("#loadPatientReportsButton").click();
    await expect(doctor.page.locator("#status")).toContainText("consent");
    await doctor.page.locator("#requestPatientAddress").fill(patient.wallet.address);
    await doctor.page.locator("#requestAccessButton").click();
    await expect(doctor.page.locator("#status")).toContainText("confirmed");
    await patient.page.locator("#refreshConsentButton").click();
    await expect(patient.page.locator("#consentActivityList")).toContainText("Doctor requests access");
    await patient.page.locator("#consentActivityList").getByRole("button", { name: "Decline" }).click();
    await expect(patient.page.locator("#consentActivityList")).toContainText("No pending requests");

    await doctor.page.locator("#requestAccessButton").click();
    await expect(doctor.page.locator("#status")).toContainText("confirmed");
    await patient.page.locator("#refreshConsentButton").click();
    await patient.page.locator("#consentActivityList").getByRole("button", { name: "Approve" }).click();
    await expect(patient.page.locator("#consentActivityList")).toContainText("Access granted");
    await doctor.page.locator("#loadPatientReportsButton").click();
    await expect(doctor.page.locator("#doctorReportsList")).toContainText("synthetic-report.txt");
    const downloadEvent = doctor.page.waitForEvent("download");
    await doctor.page.locator("#doctorReportsList").getByRole("button", { name: "Log & download" }).click();
    const downloaded = await downloadEvent;
    expect(downloaded.suggestedFilename()).toBe("synthetic-report.txt");
    expect(await fs.readFile(await downloaded.path())).toEqual(content);
    await expect(doctor.page.locator("#auditList")).toContainText("Medical report accessed");
    await patient.page.locator("#consentActivityList").getByRole("button", { name: "Revoke", exact: true }).click();
    await expect(patient.page.locator("#consentActivityList")).toContainText("No pending requests");
    const denied = await doctor.page.request.get(`/api/reports/${report.record_id}/download?patient_address=${patient.wallet.address}`);
    expect(denied.status()).toBe(403);
    await patient.page.reload();
    await expect(patient.page.locator("#dashboardView")).toBeVisible();
    await patient.page.locator("#logoutButton").click();
    await expect(patient.page.locator("#authView")).toBeVisible();
    expect((await patient.page.request.get("/api/auth/me")).status()).toBe(401);
    for (const app of applications) expect(app.errors).toEqual([]);
  } finally { for (const app of applications) await app.close(); }
});

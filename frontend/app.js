"use strict";

const BACKEND_URL = "/api";
const HARDHAT_CHAIN_ID = "0x7a69";
const HARDHAT_RPC_URL = new URL("/rpc", window.location.origin).href;
const ROLE_NAMES = { patient: "Patient", doctor: "Doctor", admin: "Administrator" };
const $ = (id) => document.getElementById(id);
const roleFromUrl = new URLSearchParams(window.location.search).get("role");
let selectedRole = Object.hasOwn(ROLE_NAMES, roleFromUrl) ? roleFromUrl : "patient";
let authMode = "login";
let sessionUser = null;
let provider, signer, contract, contractAbi;
let activeWallet = "";
let backendHealth = null;
let statusTimer;
let authBusy = false;
let walletBusy = false;
let walletChainId = "";
let walletProvider = null;
const announcedWallets = new Map();
const watchedProviders = new WeakSet();

function detectMetaMask() {
  const announced = [...announcedWallets.values()].find((item) => item.info?.rdns === "io.metamask");
  const injected = window.ethereum?.providers?.find((item) => item.isMetaMask) || window.ethereum;
  const candidate = announced?.provider || (injected?.isMetaMask ? injected : null);
  if (candidate && !sessionUser && !walletBusy) walletProvider = candidate;
  if (walletProvider) bindWalletEvents(walletProvider);
  return walletProvider;
}

$("contractAddress").value = localStorage.getItem("healthRecordsContract") || "";

function showStatus(message, isError = false) {
  if (isError && authBusy && !$("authView").hidden) {
    $("authError").textContent = message;
    $("authError").hidden = false;
  }
  clearTimeout(statusTimer);
  $("status").hidden = false;
  $("status").classList.toggle("error", isError);
  $("status").textContent = message;
  if (!isError) statusTimer = setTimeout(() => { $("status").hidden = true; }, 12000);
}

function errorMessage(error) {
  if (error?.code === -32002) return "A MetaMask request is already open. Open the extension and finish or cancel that request first.";
  if (error?.code === 4001 || error?.code === "ACTION_REJECTED") return "The wallet request was cancelled. You can try again.";
  if (error?.name === "TimeoutError" || error?.name === "AbortError") return "The request timed out. Check the backend and IPFS node, then retry.";
  if (error instanceof TypeError && /fetch/i.test(error.message)) return "Cannot reach HealthRecords. Check that the project server is running and try again.";
  return error?.reason || error?.revert?.name || error?.shortMessage || error?.message || "The operation failed.";
}

async function readError(response) {
  try {
    const body = await response.json();
    if (Array.isArray(body.detail)) return body.detail.map((item) => item.msg).join("; ");
    return body.detail || `Request failed (${response.status}).`;
  } catch { return `Request failed (${response.status}).`; }
}

async function apiFetch(path, options = {}) {
  const response = await fetch(`${BACKEND_URL}${path}`, {
    credentials: "include", cache: "no-store", signal: AbortSignal.timeout(90000), ...options,
  });
  if (!response.ok) {
    const message = await readError(response);
    if (response.status === 401 && sessionUser) resetSessionView();
    throw new Error(message);
  }
  return response;
}

async function api(path, options = {}) {
  return (await apiFetch(path, options)).json();
}

function postJson(path, body) {
  return api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

function address(value, label = "wallet address") {
  if (!window.ethers) throw new Error("The wallet library is missing. Copy the included frontend/vendor folder into your project.");
  if (!ethers.isAddress(value)) throw new Error(`Enter a valid ${label}.`);
  return ethers.getAddress(value);
}

function recordId(value) {
  if (!/^[1-9]\d*$/.test(String(value)) || BigInt(value) > 9223372036854775807n) throw new Error("Enter a positive record ID.");
  return String(value);
}

function shortAddress(value) { return `${value.slice(0, 6)}…${value.slice(-4)}`; }

function renderAuth() {
  document.body.dataset.portalRole = selectedRole;
  const roleCopy = {
    patient: ["Your personal health workspace", "View your reports and choose who can access them.", "+"],
    doctor: ["Your patient care workspace", "Find shared records and request patient permission.", "✚"],
    admin: ["Your care network workspace", "Review registrations and verify doctor accounts.", "✓"],
  }[selectedRole];
  $("roleContextTitle").textContent = roleCopy[0];
  $("roleContextText").textContent = roleCopy[1];
  $("roleContextIcon").textContent = roleCopy[2];
  $("authTitle").textContent = authMode === "register" ? `Create a ${selectedRole} account` : `${ROLE_NAMES[selectedRole]} sign in`;
  $("authSubtitle").textContent = authMode === "register"
    ? `Create your ${ROLE_NAMES[selectedRole].toLowerCase()} account and link your wallet.`
    : "Welcome back. Your care workspace is one sign-in away.";
  document.querySelectorAll("[data-auth-role]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.authRole === selectedRole)));
  $("registerName").hidden = authMode !== "register";
  $("fullName").required = authMode === "register";
  $("password").minLength = authMode === "register" ? 12 : 1;
  $("password").autocomplete = authMode === "register" ? "new-password" : "current-password";
  $("passwordHint").hidden = authMode !== "register";
  $("authSubmitLabel").textContent = `${authMode === "register" ? "Create" : "Sign in as"} ${ROLE_NAMES[selectedRole]}${authMode === "register" ? " account" : ""}`;
  $("authSwitchLabel").textContent = authMode === "register" ? "Already have an account?" : "New to HealthRecords?";
  $("toggleAuthMode").textContent = authMode === "register" ? "Sign in" : "Create an account";
  $("roleExplanation").textContent = selectedRole === "admin"
    ? "Administrator access requires the wallet that owns the deployed contract."
    : selectedRole === "doctor"
      ? "Link your doctor wallet. An administrator verifies new doctors before patient access."
      : "Sign in with your password and confirm your patient wallet in MetaMask.";
}

function renderWorkspaceNav(role) {
  if ($("workspaceNav").dataset.role === role) return;
  const items = {
    patient: [["patientLibrary", "My reports", "▤"], ["uploadReport", "Upload report", "+"], ["patientConsent", "Doctor access", "◇"]],
    doctor: [["patientSearch", "Patient records", "▤"], ["requestConsent", "Request access", "◇"]],
    admin: [["providerDirectory", "Doctor directory", "▤"], ["verifyProvider", "Verify doctors", "✓"]],
  }[role];
  const links = [["workspaceOverview", "Overview", "◫"], ...items, ["auditTrail", "Audit trail", "↗"]];
  $("workspaceNav").replaceChildren();
  $("workspaceNav").dataset.role = role;
  links.forEach(([target, title, icon], index) => {
    const link = document.createElement("a");
    link.href = `#${target}`;
    const symbol = document.createElement("span");
    symbol.className = "nav-icon";
    symbol.setAttribute("aria-hidden", "true");
    symbol.textContent = icon;
    link.append(symbol, document.createTextNode(title));
    if (index === 0) link.setAttribute("aria-current", "location");
    link.addEventListener("click", () => {
      $("workspaceNav").querySelectorAll("a").forEach(item => item.removeAttribute("aria-current"));
      link.setAttribute("aria-current", "location");
    });
    $("workspaceNav").append(link);
  });
}

function renderDashboard() {
  if (!sessionUser) return;
  const role = sessionUser.role;
  document.body.dataset.portalRole = role;
  renderWorkspaceNav(role);
  const descriptions = {
    patient: ["Your records, your control.", "Add reports, choose your doctors, and manage your consent."],
    doctor: ["Care starts with permission.", "Request patient access and retrieve the reports you need."],
    admin: ["Keep your care network trusted.", "Review doctor accounts and manage provider verification."],
  };
  $("authView").hidden = true;
  $("dashboardView").hidden = false;
  document.querySelectorAll("[data-role]").forEach((panel) => { panel.hidden = panel.dataset.role !== role; });
  $("identityName").textContent = sessionUser.full_name;
  $("identityRole").textContent = ROLE_NAMES[role];
  $("workspaceLabel").textContent = `${ROLE_NAMES[role]} workspace`;
  $("dashboardTitle").textContent = descriptions[role][0];
  $("dashboardDescription").textContent = descriptions[role][1];
  $("dashboardWallet").textContent = shortAddress(sessionUser.wallet_address);
  $("dashboardWallet").title = sessionUser.wallet_address;
  const pending = role === "doctor" && sessionUser.status !== "verified";
  $("doctorPendingNotice").hidden = !pending;
  $("roleStatusBadge").textContent = pending ? "Awaiting approval" : role === "doctor" ? "Verified doctor" : ROLE_NAMES[role];
  $("roleStatusBadge").classList.toggle("pending", pending);
  document.querySelectorAll("[data-doctor-action]").forEach((button) => { button.disabled = pending || button.getAttribute("aria-busy") === "true"; });
  $("primaryMetricLabel").textContent = role === "admin" ? "Registered doctors" : role === "doctor" ? "Shared reports found" : "My reports";
  $("auditHint").textContent = role === "admin" ? "Recent public events from this HealthRecords contract." : "Recent public blockchain events involving your wallet.";
  updateServiceLabels();
}

function resetSessionView() {
  sessionUser = null;
  $("dashboardView").hidden = true;
  $("authView").hidden = false;
  $("password").value = "";
  $("authError").hidden = true;
  $("workspaceNav").replaceChildren();
  delete $("workspaceNav").dataset.role;
  $("primaryMetricValue").textContent = "—";
  for (const id of ["patientReportsList", "doctorReportsList", "doctorsList", "auditList", "consentActivityList"]) $(id).replaceChildren();
  $("medicalFile").value = "";
  $("recordMetadata").value = "";
  $("retryRecordId").value = "";
  $("emergencyReason").value = "";
  renderAuth();
}

async function logout(notify = true) {
  localStorage.setItem("healthRecordsLogoutPending", "1");
  let revoked = false;
  try {
    await api("/auth/logout", { method: "POST" });
    localStorage.removeItem("healthRecordsLogoutPending");
    revoked = true;
  } catch { /* Retry revocation before any automatic session restoration. */ }
  resetSessionView();
  if (notify) showStatus(revoked ? "You have signed out." : "Workspace locked. Restart the backend so the saved session can be signed out.", !revoked);
}

function updateServiceLabels() {
  const ready = backendHealth?.hardhat_connected && backendHealth?.contract_configured && backendHealth?.encryption_configured;
  $("backendStatus").textContent = backendHealth ? (ready ? "Ready" : "Check configuration") : "Not running";
  $("ipfsStatus").textContent = backendHealth?.ipfs_connected ? "Connected" : "Offline";
  $("dashboardConnection").textContent = `${ready ? "Backend ready" : "Backend unavailable"} · IPFS ${backendHealth?.ipfs_connected ? "connected" : "offline"}`;
  $("storageMetric").textContent = backendHealth?.ipfs_connected ? "Encrypted IPFS · online" : "Encrypted IPFS · offline";
  $("storageNotice").hidden = !sessionUser || Boolean(backendHealth?.ipfs_connected);
  $("storageNotice").textContent = backendHealth?.ipfs_error || "Report storage is offline. Restart the project server and try again.";
  $("serviceSummary").textContent = !backendHealth ? "Services unavailable · start the project server" : ready && backendHealth.ipfs_connected
    ? "All services connected · ready to sign in" : "Some services need attention · check connection settings";
  $("serviceSummary").dataset.ready = String(Boolean(ready && backendHealth?.ipfs_connected));
  if (backendHealth?.max_file_size_mb) $("fileSizeHint").textContent = `Maximum file size: ${backendHealth.max_file_size_mb} MB.`;
}

async function checkBackend() {
  try {
    const response = await fetch(`${BACKEND_URL}/health`, { cache: "no-store", signal: AbortSignal.timeout(10000) });
    if (!response.ok) throw new Error("Backend unavailable");
    backendHealth = await response.json();
    if (!$("contractAddress").value && backendHealth.contract_address) {
      $("contractAddress").value = backendHealth.contract_address;
    }
  } catch { backendHealth = null; }
  updateServiceLabels();
  return backendHealth;
}

async function loadAbi() {
  if (!contractAbi) {
    const response = await fetch("./abi.json", { cache: "no-store" });
    if (!response.ok) throw new Error("frontend/abi.json is missing. Copy the ABI included in the update.");
    contractAbi = await response.json();
  }
  return contractAbi;
}

async function connectWalletAccount(silent = false, onProgress = () => {}) {
  detectMetaMask();
  if (!walletProvider) throw new Error("MetaMask was not detected. Install the browser extension, unlock it, and refresh this page.");
  onProgress("account");
  const accounts = await walletProvider.request({ method: silent ? "eth_accounts" : "eth_requestAccounts" });
  if (!accounts.length) throw new Error("Unlock MetaMask and choose your wallet account.");
  onProgress("network");
  let chainId = await walletProvider.request({ method: "eth_chainId" });
  if (chainId.toLowerCase() !== HARDHAT_CHAIN_ID) {
    if (silent) throw new Error("Select Hardhat Local in MetaMask.");
    try {
      await walletProvider.request({ method: "wallet_switchEthereumChain", params: [{ chainId: HARDHAT_CHAIN_ID }] });
    } catch (error) {
      if (error.code !== 4902) throw error;
      await walletProvider.request({ method: "wallet_addEthereumChain", params: [{ chainId: HARDHAT_CHAIN_ID,
        chainName: "Hardhat Local", nativeCurrency: { name: "Ethereum", symbol: "ETH", decimals: 18 }, rpcUrls: [HARDHAT_RPC_URL] }] });
      // Adding a network does not guarantee that the wallet selected it.
      if ((await walletProvider.request({ method: "eth_chainId" })).toLowerCase() !== HARDHAT_CHAIN_ID) {
        await walletProvider.request({ method: "wallet_switchEthereumChain", params: [{ chainId: HARDHAT_CHAIN_ID }] });
      }
    }
  }
  chainId = await walletProvider.request({ method: "eth_chainId" });
  if (chainId.toLowerCase() !== HARDHAT_CHAIN_ID) throw new Error("Hardhat Local was not selected. Retry and approve the network switch in MetaMask.");
  const currentAccounts = await walletProvider.request({ method: "eth_accounts" });
  if (!currentAccounts.length) throw new Error("The wallet was disconnected. Connect again to continue.");
  activeWallet = currentAccounts[0];
  walletChainId = chainId.toLowerCase();
  updateWalletGui();
  onProgress("ready");
  return activeWallet;
}

async function connectWallet(silent = false) {
  if (!window.ethers) throw new Error("The wallet library is missing. Copy frontend/vendor from the update.");
  await connectWalletAccount(silent);
  provider = new ethers.BrowserProvider(walletProvider);
  signer = await provider.getSigner();
  activeWallet = await signer.getAddress();
  const contractAddress = address($("contractAddress").value.trim(), "contract address");
  if (await provider.getCode(contractAddress) === "0x") throw new Error(`No contract exists at this address. In MetaMask, set Hardhat Local's RPC URL to ${HARDHAT_RPC_URL}, then use the current deployment address.`);
  contract = new ethers.Contract(contractAddress, await loadAbi(), signer);
  localStorage.setItem("healthRecordsContract", contractAddress);
  updateWalletGui();
  return activeWallet;
}

function setWalletProgress(stage) {
  const positions = { waiting: -1, account: 0, network: 1, ready: 3 };
  const index = positions[stage];
  ["Account", "Network", "Ready"].forEach((name, i) => {
    const state = i < index ? "done" : i === index ? "active" : "waiting";
    $("walletStep" + name).dataset.state = state;
    $("walletStep" + name + "State").textContent = state === "done" ? "Done" : state === "active" ? "In progress" : "Waiting";
  });
}

function updateWalletGui() {
  const ready = Boolean(activeWallet) && walletChainId === HARDHAT_CHAIN_ID;
  $("walletCardTitle").textContent = ready ? "MetaMask connected" : activeWallet ? "Switch your wallet network" : "Connect your wallet";
  $("walletCardSubtitle").textContent = ready ? `${shortAddress(activeWallet)} · Hardhat Local` : "Use MetaMask to confirm your identity";
  $("walletCardAction").textContent = ready ? "Manage →" : "Connect →";
  $("openWalletDialog").dataset.connected = String(ready);
  $("walletAddress").textContent = activeWallet || "Not connected";
  $("networkName").textContent = walletChainId ? (walletChainId === HARDHAT_CHAIN_ID ? "Hardhat Local · 31337" : `Different network (${walletChainId})`) : "Not connected";
  $("walletConnectedDetails").hidden = !activeWallet;
  $("walletModalAddress").textContent = activeWallet;
  $("walletModalNetwork").textContent = walletChainId === HARDHAT_CHAIN_ID ? "Hardhat Local · 31337" : walletChainId ? "Switch to Hardhat Local" : "Not connected";
  $("walletModalContinue").hidden = !ready;
  $("walletModalContinue").textContent = sessionUser ? "Back to workspace →" : "Continue to sign in →";
  $("walletModalConnect").hidden = ready;
  $("walletDialogTitle").textContent = ready ? "You're connected." : "Connect your wallet";
  $("walletDialogDescription").textContent = ready ? "This account will confirm your identity when you sign in or approve a record action." : "Your wallet confirms it's you. Choose MetaMask below and approve the connection in its popup.";
  if (ready && !walletBusy) setWalletProgress("ready");
}

async function readWalletSnapshot() {
  detectMetaMask();
  if (!walletProvider) { activeWallet = ""; walletChainId = ""; updateWalletGui(); return; }
  const [accounts, chainId] = await Promise.all([
    walletProvider.request({ method: "eth_accounts" }),
    walletProvider.request({ method: "eth_chainId" }),
  ]);
  activeWallet = accounts[0] || "";
  walletChainId = chainId.toLowerCase();
  updateWalletGui();
}

async function openWalletPanel() {
  if (authBusy) return;
  detectMetaMask();
  if (!$("walletDialog").open) $("walletDialog").showModal();
  if (walletBusy) return;
  $("walletModalError").hidden = true;
  $("walletInstallLink").hidden = Boolean(walletProvider);
  $("walletModalConnect").disabled = !walletProvider;
  $("walletProviderHint").textContent = walletProvider ? "Connect browser extension" : "Extension not detected";
  setWalletProgress("waiting");
  try {
    await readWalletSnapshot();
    $("walletModalStatus").textContent = activeWallet && walletChainId === HARDHAT_CHAIN_ID
      ? "Wallet ready. Your app account still needs its own sign-in."
      : walletProvider ? "Click MetaMask to begin. Keep its popup open to approve the request." : "Install MetaMask in this browser, then refresh this page.";
  } catch (error) {
    $("walletModalError").textContent = errorMessage(error);
    $("walletModalError").hidden = false;
  }
}

async function connectFromWalletPanel() {
  if (walletBusy || authBusy) return;
  walletBusy = true;
  $("walletModalConnect").disabled = true;
  $("walletModalContinue").hidden = true;
  $("authSubmitButton").disabled = true;
  $("walletModalError").hidden = true;
  $("walletDialog").setAttribute("aria-busy", "true");
  try {
    await connectWalletAccount(false, (stage) => {
      setWalletProgress(stage);
      $("walletModalStatus").textContent = stage === "account" ? "Open MetaMask and approve sharing your account address."
        : stage === "network" ? "Approve Hardhat Local if MetaMask asks to add or switch networks."
        : "Wallet connected. You can continue to sign in.";
    });
  } catch (error) {
    try { await readWalletSnapshot(); } catch { /* Preserve the original wallet error. */ }
    $("walletModalError").textContent = errorMessage(error);
    $("walletModalError").hidden = false;
    $("walletModalStatus").textContent = "Connection not completed. You can retry below.";
    $("walletProviderHint").textContent = "Try connecting again";
    setWalletProgress("waiting");
  } finally {
    walletBusy = false;
    $("walletModalConnect").disabled = false;
    $("authSubmitButton").disabled = false;
    $("walletDialog").removeAttribute("aria-busy");
    updateWalletGui();
  }
}

async function requireRole(...roles) {
  if (!sessionUser || !roles.includes(sessionUser.role)) throw new Error("Sign in with the correct account role to perform this action.");
  if (!contract || !signer) throw new Error("Connect your linked MetaMask wallet first.");
  const accounts = await walletProvider.request({ method: "eth_accounts" });
  if (!accounts.length || accounts[0].toLowerCase() !== sessionUser.wallet_address.toLowerCase()) {
    await logout(false);
    throw new Error("Your MetaMask account changed. Sign in with the matching account.");
  }
  if ((await provider.getNetwork()).chainId !== 31337n) throw new Error("Switch MetaMask to Hardhat Local.");
}

async function refreshIdentity() {
  if (!sessionUser) return;
  const result = await api("/auth/me");
  sessionUser = result.user;
  renderDashboard();
}

async function runAction(button, action) {
  if (button.disabled) return;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try { return await action(); }
  catch (error) { console.error(error); showStatus(errorMessage(error), true); return null; }
  finally {
    button.removeAttribute("aria-busy");
    button.disabled = button.hasAttribute("data-doctor-action") && sessionUser?.role === "doctor" && sessionUser.status !== "verified";
  }
}

async function sendTransaction(message, send) {
  showStatus(`${message} Confirm the transaction in MetaMask.`);
  const transaction = await send();
  showStatus("Transaction submitted. Waiting for confirmation…");
  const receipt = await transaction.wait();
  await refreshAuditLog();
  return receipt;
}

async function signIn(event) {
  event.preventDefault();
  if (authBusy || walletBusy) return;
  authBusy = true;
  $("authError").hidden = true;
  $("authForm").setAttribute("aria-busy", "true");
  document.querySelectorAll("[data-auth-role], #toggleAuthMode").forEach((button) => { button.disabled = true; });
  try {
    await runAction($("authSubmitButton"), async () => {
      const submitted = { email: $("email").value.trim().toLowerCase(), password: $("password").value,
        full_name: $("fullName").value.trim(), role: selectedRole, mode: authMode };
      const health = await checkBackend();
      if (!health?.contract_configured) {
        $("setupDetails").open = true;
        throw new Error("The project services are not ready. Start the project server and retry when all services are connected.");
      }
      if (!$("contractAddress").value) $("contractAddress").value = health.contract_address;
      if (localStorage.getItem("healthRecordsLogoutPending")) {
        await logout(false);
        if (localStorage.getItem("healthRecordsLogoutPending")) throw new Error("The previous session could not be signed out. Check the backend and try again.");
      }
      const wallet = await connectWallet();
      const { role, mode, email } = submitted;
      showStatus("Confirm the account sign-in message in MetaMask.");
      const challenge = await postJson("/auth/challenge", { wallet_address: wallet, email, role,
        action: mode, contract_address: await contract.getAddress() });
      const signature = await signer.signMessage(challenge.message);
      const result = await postJson(`/auth/${mode}`, { challenge_id: challenge.challenge_id, signature,
        email, password: submitted.password, wallet_address: wallet, role, full_name: submitted.full_name });
      sessionUser = result.user;
      $("password").value = "";
      renderDashboard();
      showStatus(`Welcome, ${sessionUser.full_name}.`);
      await loadWorkspace();
    });
  } finally {
    authBusy = false;
    $("authForm").removeAttribute("aria-busy");
    document.querySelectorAll("[data-auth-role], #toggleAuthMode").forEach((button) => { button.disabled = false; });
  }
}

function textElement(tag, text, className = "") {
  const element = document.createElement(tag);
  element.textContent = text;
  if (className) element.className = className;
  return element;
}

function empty(container, text) {
  container.className = "empty";
  container.textContent = text;
}

function renderReports(container, reports, patientWallet) {
  if (!reports.length) { empty(container, "No stored reports found for this wallet on the current blockchain."); return; }
  container.className = "table-wrap";
  const table = document.createElement("table");
  const head = document.createElement("thead");
  const header = document.createElement("tr");
  for (const title of ["Report", "Storage", "Action"]) header.appendChild(textElement("th", title));
  head.appendChild(header); table.appendChild(head);
  const body = document.createElement("tbody");
  for (const report of reports) {
    const row = document.createElement("tr");
    const name = document.createElement("td");
    name.appendChild(textElement("span", report.original_filename, "record-name"));
    name.appendChild(textElement("small", `Record #${report.record_id} · ${report.file_size ? `${(report.file_size / 1024).toFixed(1)} KB` : "Legacy report"}`));
    const storage = document.createElement("td");
    storage.appendChild(textElement("span", report.storage_backend === "ipfs" ? "Encrypted IPFS" : "Encrypted local file", "badge"));
    if (report.cid) storage.appendChild(textElement("span", report.cid, "cid"));
    const actions = document.createElement("td");
    const button = textElement("button", sessionUser.role === "doctor" ? "Log & download" : "Download", "secondary");
    button.type = "button";
    button.addEventListener("click", () => runAction(button, () => downloadReport(patientWallet, String(report.record_id))));
    actions.appendChild(button);
    row.append(name, storage, actions); body.appendChild(row);
  }
  table.appendChild(body); container.replaceChildren(table);
}

async function refreshPatientReports() {
  await requireRole("patient");
  const result = await api("/reports");
  renderReports($("patientReportsList"), result.reports, sessionUser.wallet_address);
  $("primaryMetricValue").textContent = String(result.reports.length);
}

async function refreshConsentActivity() {
  await requireRole("patient");
  const container = $("consentActivityList");
  const wallet = sessionUser.wallet_address.toLowerCase();
  const latest = Number(await provider.send("eth_blockNumber", []));
  const logs = await provider.getLogs({ address: await contract.getAddress(), fromBlock: 0, toBlock: latest });
  const ids = new Set();
  for (const log of logs) {
    try {
      const event = contract.interface.parseLog(log);
      if (event?.args.patient?.toLowerCase() === wallet && event.args.accessId) ids.add(event.args.accessId.toString());
    } catch { /* Only consent events have an access ID and patient. */ }
  }
  const records = await Promise.all([...ids].reverse().map((id) => contract.getAccessRecord(BigInt(id))));
  const current = records.filter((item) => item.status === 0n || (item.status === 1n && (!item.expiresAt || item.expiresAt > BigInt(Math.floor(Date.now() / 1000)))));
  if (!current.length) { empty(container, "No pending requests or active doctor permissions. You can grant access using a doctor's wallet above."); return; }
  container.className = "consent-activity-list";
  container.replaceChildren();
  for (const item of current) {
    const entry = document.createElement("div"); entry.className = "consent-entry";
    const details = document.createElement("div");
    details.appendChild(textElement("strong", item.status === 0n ? "Doctor requests access" : item.accessType === 1n ? "Emergency access" : "Access granted"));
    details.appendChild(textElement("span", item.doctor, "cid"));
    details.appendChild(textElement("small", `Access #${item.id}${item.expiresAt ? ` · Expires ${new Date(Number(item.expiresAt) * 1000).toLocaleString()}` : ""}${item.flaggedForReview ? " · Flagged for review" : ""}`));
    const actions = document.createElement("div"); actions.className = "inline-actions";
    const addAction = (label, css, send) => {
      const button = textElement("button", label, css); button.type = "button";
      button.addEventListener("click", () => runAction(button, async () => {
        await requireRole("patient");
        await sendTransaction("Updating doctor permission.", send);
        await refreshConsentActivity();
        showStatus("Doctor permission updated.");
      }));
      actions.appendChild(button);
    };
    if (item.status === 0n) {
      addAction("Approve", "primary", () => contract.grantAccess(item.doctor));
      addAction("Decline", "secondary", () => contract.rejectAccessRequest(item.id));
    } else {
      addAction("Revoke", "danger", () => contract.revokeAccess(item.doctor));
      if (item.accessType === 1n && !item.flaggedForReview) addAction("Flag for review", "secondary", () => contract.flagForReview(item.id));
    }
    entry.append(details, actions); container.appendChild(entry);
  }
}

async function loadPatientReports() {
  await requireRole("doctor");
  const patient = address($("accessPatientAddress").value.trim(), "patient wallet address");
  const result = await api(`/reports?patient_address=${encodeURIComponent(patient)}`);
  renderReports($("doctorReportsList"), result.reports, patient);
  $("primaryMetricValue").textContent = String(result.reports.length);
}

async function refreshDoctors() {
  await requireRole("admin");
  const result = await api("/admin/doctors");
  $("primaryMetricValue").textContent = String(result.doctors.length);
  const container = $("doctorsList");
  if (!result.doctors.length) { empty(container, "Doctor registrations will appear here after an account is created."); return; }
  container.className = "table-wrap";
  const table = document.createElement("table");
  const head = document.createElement("thead"); const heading = document.createElement("tr");
  for (const title of ["Doctor", "Status", "Action"]) heading.appendChild(textElement("th", title));
  head.appendChild(heading); table.appendChild(head);
  const body = document.createElement("tbody");
  for (const doctor of result.doctors) {
    const row = document.createElement("tr"); const name = document.createElement("td");
    name.append(textElement("span", doctor.full_name, "record-name"), textElement("small", doctor.email), textElement("span", doctor.wallet_address, "cid"));
    const status = document.createElement("td"); status.appendChild(textElement("span", doctor.verified ? "Verified" : "Pending", `badge${doctor.verified ? "" : " pending"}`));
    const action = document.createElement("td"); const button = textElement("button", doctor.verified ? "Remove" : "Verify", doctor.verified ? "danger" : "secondary");
    button.type = "button";
    button.addEventListener("click", () => runAction(button, () => verifyDoctor(doctor.wallet_address, !doctor.verified)));
    action.appendChild(button); row.append(name, status, action); body.appendChild(row);
  }
  table.appendChild(body); container.replaceChildren(table);
}

async function verifyDoctor(wallet, verified) {
  await requireRole("admin");
  const doctor = address(wallet, "doctor wallet address");
  await sendTransaction(verified ? "Verifying doctor." : "Removing doctor verification.", () => contract.setVerifiedProvider(doctor, verified));
  await refreshDoctors();
  showStatus(verified ? "Doctor verified. They can now request patient access." : "Doctor verification removed. Future downloads are blocked.");
}

function anchoredId(receipt) {
  for (const log of receipt.logs) {
    try {
      const parsed = contract.interface.parseLog(log);
      if (parsed?.name === "MedicalRecordAnchored") return parsed.args.recordId.toString();
    } catch { /* An unrelated log is not a record anchor. */ }
  }
  throw new Error("Could not read the new record ID. Check the transaction in the audit trail.");
}

async function uploadReport() {
  await requireRole("patient");
  await refreshIdentity();
  const health = await checkBackend();
  if (!health) throw new Error("Cannot reach HealthRecords. Restart the project server and try again.");
  if (!health?.encryption_configured) throw new Error("Configure the existing FERNET_KEY in backend/.env before uploading.");
  if (!health?.ipfs_connected) throw new Error(health.ipfs_error || "Start IPFS Desktop or run ipfs daemon before uploading.");
  const file = $("medicalFile").files[0];
  if (!file || !file.size) throw new Error("Select a non-empty medical report.");
  if (file.size > (health.max_file_size_mb || 10) * 1024 * 1024) throw new Error(`Choose a report smaller than ${health.max_file_size_mb || 10} MB.`);
  const hash = ethers.keccak256(new Uint8Array(await file.arrayBuffer()));
  let id = $("retryRecordId").value.trim();
  if (id) id = recordId(id);
  else {
    const existing = await contract.recordIdByHash(hash);
    if (existing !== 0n) id = existing.toString();
  }
  if (id) {
    const existing = await contract.getMedicalRecord(BigInt(id));
    if (existing.patient.toLowerCase() !== sessionUser.wallet_address.toLowerCase() || existing.fileHash.toLowerCase() !== hash.toLowerCase()) {
      throw new Error("This file or record ID belongs to a different on-chain record. Choose the correct report.");
    }
    showStatus(`Retrying encrypted storage for record #${id}.`);
  } else {
    const label = $("recordMetadata").value.trim() || file.name;
    const metadataHash = ethers.keccak256(ethers.toUtf8Bytes(label));
    const receipt = await sendTransaction("Anchoring the report hash.", () => contract.anchorMedicalRecord(hash, metadataHash));
    id = anchoredId(receipt);
  }
  $("retryRecordId").value = id;
  showStatus(`Record #${id} anchored. Encrypting and pinning the file to IPFS…`);
  const body = new FormData();
  body.append("file", file); body.append("record_id", id); body.append("file_hash", hash);
  try {
    const result = await api("/reports", { method: "POST", body });
    showStatus(`Record #${id} encrypted and pinned to IPFS. CID: ${result.cid}`);
    $("medicalFile").value = ""; $("recordMetadata").value = ""; $("retryRecordId").value = "";
    await refreshPatientReports();
  } catch (error) {
    await refreshPatientReports().catch(() => {});
    throw new Error(`Record #${id} is anchored. ${errorMessage(error)} Keep the same file selected and retry to finish storage.`);
  }
}

async function downloadReport(patientValue, idValue) {
  await requireRole("patient", "doctor");
  const patient = address(patientValue, "patient wallet address");
  const id = recordId(idValue);
  const query = new URLSearchParams({ patient_address: patient });
  if (sessionUser.role === "doctor") {
    await refreshIdentity();
    if (sessionUser.status !== "verified") throw new Error("An administrator must verify your doctor wallet first.");
    const receipt = await sendTransaction("Logging report access on-chain.", () => contract.logRecordAccess(patient, BigInt(id)));
    query.set("audit_tx_hash", receipt.hash);
  }
  showStatus("Checking permission and retrieving your encrypted report…");
  const response = await apiFetch(`/reports/${id}/download?${query}`);
  const disposition = response.headers.get("Content-Disposition") || "";
  const match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  const filename = match ? decodeURIComponent(match[1]) : `medical-report-${id}`;
  const objectUrl = URL.createObjectURL(await response.blob());
  const link = document.createElement("a"); link.href = objectUrl; link.download = filename;
  document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  showStatus(`Report #${id} verified and downloaded.`);
}

const EVENT_LABELS = { MedicalRecordAnchored: "Medical report anchored", ProviderVerificationUpdated: "Doctor verification updated",
  AccessRequested: "Patient access requested", AccessGranted: "Patient consent granted", AccessRevoked: "Patient access revoked",
  AccessRequestRejected: "Access request declined", EmergencyAccessGranted: "Emergency access granted",
  EmergencyAccessExpired: "Emergency access expired", EmergencyAccessFlagged: "Emergency access flagged for review",
  RecordAccessed: "Medical report accessed", AdminTransferred: "Administrator updated" };

async function refreshAuditLog() {
  if (!sessionUser || !contract || !provider) return;
  const container = $("auditList");
  try {
    const latest = Number(await provider.send("eth_blockNumber", []));
    const logs = await provider.getLogs({ address: await contract.getAddress(), fromBlock: Math.max(0, latest - 5000), toBlock: latest });
    const entries = [];
    for (const log of [...logs].reverse()) {
      let parsed;
      try { parsed = contract.interface.parseLog(log); } catch { continue; }
      if (!parsed) continue;
      if (sessionUser.role !== "admin" && !Array.from(parsed.args).some((value) => typeof value === "string" && value.toLowerCase() === sessionUser.wallet_address.toLowerCase())) continue;
      const entry = document.createElement("div"); entry.className = "audit-entry";
      entry.appendChild(textElement("span", "", "audit-marker"));
      const details = document.createElement("div"); details.appendChild(textElement("strong", EVENT_LABELS[parsed.name] || parsed.name));
      const values = parsed.fragment.inputs.map((input, index) => {
        let value = parsed.args[index];
        if (typeof value === "bigint") value = value.toString();
        if (input.type === "address") value = shortAddress(String(value));
        if (input.type === "bytes32") value = `${String(value).slice(0, 12)}…`;
        return `${input.name}: ${String(value)}`;
      });
      details.appendChild(textElement("p", `Block ${log.blockNumber} · ${values.join(" · ")}`));
      entry.appendChild(details); entries.push(entry);
      if (entries.length >= 50) break;
    }
    if (!entries.length) empty(container, "No recent blockchain events for this workspace.");
    else { container.className = ""; container.replaceChildren(...entries); }
  } catch (error) { empty(container, errorMessage(error)); }
}

async function loadWorkspace() {
  if (sessionUser.role === "patient") { await refreshPatientReports(); await refreshConsentActivity(); }
  if (sessionUser.role === "admin") await refreshDoctors();
  if (sessionUser.role === "doctor") $("primaryMetricValue").textContent = "0";
  await refreshAuditLog();
}

document.querySelectorAll("[data-auth-role]").forEach((button) => button.addEventListener("click", () => {
  selectedRole = button.dataset.authRole;
  $("password").value = "";
  $("authError").hidden = true;
  const url = new URL(window.location.href);
  url.searchParams.set("role", selectedRole);
  url.hash = "";
  window.history.replaceState(null, "", url);
  renderAuth();
}));
$("toggleAuthMode").addEventListener("click", () => {
  authMode = authMode === "login" ? "register" : "login";
  $("password").value = "";
  $("authError").hidden = true;
  renderAuth();
});
$("togglePassword").addEventListener("click", () => {
  const visible = $("password").type === "password";
  $("password").type = visible ? "text" : "password";
  $("togglePassword").textContent = visible ? "Hide" : "Show";
  $("togglePassword").setAttribute("aria-label", visible ? "Hide password" : "Show password");
});
$("authForm").addEventListener("submit", signIn);
$("logoutButton").addEventListener("click", () => runAction($("logoutButton"), () => logout()));
$("connectWalletButton").addEventListener("click", openWalletPanel);
$("openWalletDialog").addEventListener("click", openWalletPanel);
$("dashboardWalletButton").addEventListener("click", openWalletPanel);
$("closeWalletDialog").addEventListener("click", () => $("walletDialog").close());
$("walletModalContinue").addEventListener("click", () => {
  $("walletDialog").close();
  if (!sessionUser) $("email").focus();
});
$("walletModalConnect").addEventListener("click", connectFromWalletPanel);
$("saveContractButton").addEventListener("click", () => runAction($("saveContractButton"), async () => {
  const value = address($("contractAddress").value.trim(), "contract address");
  localStorage.setItem("healthRecordsContract", value); contract = undefined;
  showStatus("Contract address saved. Choose your role and sign in.");
}));
$("anchorRecordButton").addEventListener("click", () => runAction($("anchorRecordButton"), uploadReport));
$("refreshReportsButton").addEventListener("click", () => runAction($("refreshReportsButton"), refreshPatientReports));
$("refreshConsentButton").addEventListener("click", () => runAction($("refreshConsentButton"), refreshConsentActivity));
$("refreshDoctorsButton").addEventListener("click", () => runAction($("refreshDoctorsButton"), refreshDoctors));
$("verifyProviderButton").addEventListener("click", () => runAction($("verifyProviderButton"), () => verifyDoctor($("providerAddress").value.trim(), true)));
$("unverifyProviderButton").addEventListener("click", () => runAction($("unverifyProviderButton"), () => verifyDoctor($("providerAddress").value.trim(), false)));
$("loadPatientReportsButton").addEventListener("click", () => runAction($("loadPatientReportsButton"), loadPatientReports));
$("logAccessButton").addEventListener("click", () => runAction($("logAccessButton"), () => downloadReport($("accessPatientAddress").value.trim(), $("accessRecordId").value.trim())));

function wireTransaction(buttonId, role, message, argumentsForTransaction) {
  $(buttonId).addEventListener("click", () => runAction($(buttonId), async () => {
    await requireRole(role);
    const send = argumentsForTransaction();
    await sendTransaction(message, send);
    await refreshIdentity();
    if (role === "patient") await refreshConsentActivity();
    showStatus("Permission update confirmed on the blockchain.");
  }));
}
wireTransaction("grantAccessButton", "patient", "Granting doctor access.", () => {
  const doctor = address($("grantDoctorAddress").value.trim(), "doctor wallet address"); return () => contract.grantAccess(doctor);
});
wireTransaction("revokeAccessButton", "patient", "Revoking doctor access.", () => {
  const doctor = address($("revokeDoctorAddress").value.trim(), "doctor wallet address"); return () => contract.revokeAccess(doctor);
});
wireTransaction("flagEmergencyButton", "patient", "Flagging emergency access for review.", () => {
  const id = recordId($("emergencyAccessId").value.trim()); return () => contract.flagForReview(BigInt(id));
});
wireTransaction("requestAccessButton", "doctor", "Requesting patient permission.", () => {
  const patient = address($("requestPatientAddress").value.trim(), "patient wallet address"); return () => contract.requestAccess(patient);
});
wireTransaction("emergencyAccessButton", "doctor", "Requesting emergency access.", () => {
  const patient = address($("emergencyPatientAddress").value.trim(), "patient wallet address");
  const reason = $("emergencyReason").value.trim();
  if (!reason || ethers.toUtf8Bytes(reason).length > 280) throw new Error("Enter an emergency reason of 1–280 UTF-8 bytes.");
  return () => contract.requestEmergencyAccess(patient, reason);
});
$("refreshAuditButton").addEventListener("click", () => runAction($("refreshAuditButton"), async () => {
  await refreshIdentity(); await refreshAuditLog();
  if (sessionUser?.role === "patient") await refreshConsentActivity();
  showStatus("Workspace permissions and audit trail refreshed.");
}));

function bindWalletEvents(injected) {
  if (!injected?.on || watchedProviders.has(injected)) return;
  watchedProviders.add(injected);
  injected.on("accountsChanged", async (accounts) => {
    if (injected !== walletProvider) return;
    if (sessionUser && (!accounts.length || accounts[0].toLowerCase() !== sessionUser.wallet_address.toLowerCase())) {
      await logout(false); showStatus("Wallet changed. Sign in with the account linked to this wallet.");
    }
    activeWallet = accounts[0] || "";
    signer = contract = provider = undefined;
    updateWalletGui();
  });
  injected.on("chainChanged", async (chainId) => {
    if (injected !== walletProvider) return;
    walletChainId = chainId.toLowerCase();
    updateWalletGui();
    if (sessionUser) await logout(false);
    signer = contract = provider = undefined;
    if (!walletBusy) showStatus("Network changed. Connect to Hardhat Local and sign in again.");
  });
}

window.addEventListener("eip6963:announceProvider", (event) => {
  if (!event.detail?.info?.uuid || !event.detail?.provider?.request) return;
  announcedWallets.set(event.detail.info.uuid, event.detail);
  detectMetaMask();
});
window.dispatchEvent(new Event("eip6963:requestProvider"));

async function initialize() {
  $("walletRpcUrl").textContent = HARDHAT_RPC_URL;
  renderAuth();
  updateWalletGui();
  try { await readWalletSnapshot(); } catch { /* The connection panel displays wallet errors on demand. */ }
  await checkBackend();
  if (!backendHealth || !walletProvider) return;
  if (localStorage.getItem("healthRecordsLogoutPending")) {
    await logout(false);
    return;
  }
  try {
    const result = await api("/auth/me");
    $("contractAddress").value = result.user.contract_address;
    await connectWallet(true);
    if (activeWallet.toLowerCase() !== result.user.wallet_address.toLowerCase()) return;
    sessionUser = result.user; selectedRole = sessionUser.role;
    renderDashboard(); await loadWorkspace();
  } catch { /* A new browser session starts at the login page. */ }
}
initialize().catch((error) => showStatus(errorMessage(error), true));
setInterval(async () => {
  await checkBackend();
  if (sessionUser && !authBusy) {
    try {
      await refreshIdentity();
      if (sessionUser?.role === "patient") await refreshConsentActivity();
    } catch (error) { showStatus(errorMessage(error), true); }
  }
}, 30000);

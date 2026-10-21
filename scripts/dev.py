"""Start and supervise the local chain, encrypted IPFS store, and web application."""
from pathlib import Path
import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import Request, ProxyHandler, build_opener
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
HTTP = build_opener(ProxyHandler({}))


def request(url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    with HTTP.open(Request(url, data=data, headers={"Content-Type": "application/json"}), timeout=3) as response:
        return json.load(response)


def occupied(port):
    with socket.socket() as connection:
        return connection.connect_ex(("127.0.0.1", port)) == 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5500)
    args = parser.parse_args()

    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    python = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    node = shutil.which("node")
    if not python.exists() or not (ROOT / "node_modules/hardhat").is_dir():
        raise RuntimeError("Run npm run setup first.")
    local = ROOT / ".local"
    local.mkdir(mode=0o700, exist_ok=True)
    environment = {**os.environ, "IPFS_PATH": str(local / "ipfs"),
                   "XDG_CACHE_HOME": str(ROOT / ".cache"), "XDG_CONFIG_HOME": str(local / "config"),
                   "XDG_DATA_HOME": str(local / "share"),
                   "HARDHAT_DISABLE_TELEMETRY_PROMPT": "true"}
    children, logs = [], []

    def run(command):
        subprocess.run([str(arg) for arg in command], cwd=ROOT, env=environment, check=True)

    def start(name, command):
        log = (local / (name + ".log")).open("ab")
        logs.append(log)
        process = subprocess.Popen([str(arg) for arg in command], cwd=ROOT, env=environment,
                                   stdout=log, stderr=subprocess.STDOUT)
        children.append((name, process))
        return process

    def ready(label, check):
        deadline = time.monotonic() + 60
        while True:
            for name, process in children:
                if process.poll() is not None:
                    raise RuntimeError(f"{name} stopped. Inspect .local/{name}.log.")
            try:
                if check():
                    print(label + " ready.", flush=True)
                    return
            except (OSError, URLError, ValueError):
                pass
            if time.monotonic() > deadline:
                raise RuntimeError(label + " did not become ready; inspect .local/*.log.")
            time.sleep(0.25)

    url = f"http://127.0.0.1:{args.port}"
    try:
        if occupied(args.port):
            raise RuntimeError(f"Port {args.port} is already in use. Stop that server or use --port.")
        if occupied(8545):
            chain = request("http://127.0.0.1:8545", {"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []})
            version = request("http://127.0.0.1:8545", {"jsonrpc": "2.0", "id": 2, "method": "web3_clientVersion", "params": []})
            if chain.get("result") != "0x7a69" or "hardhat" not in version.get("result", "").lower():
                raise RuntimeError("Port 8545 must be a local Hardhat development node (31337).")
            print("Reusing local Hardhat node.", flush=True)
        else:
            start("chain", [node, ROOT / "node_modules/hardhat/internal/cli/cli.js", "node", "--hostname", "127.0.0.1"])
        ready("Blockchain", lambda: request("http://127.0.0.1:8545", {"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}).get("result") == "0x7a69")
        run([node, "scripts/compile.cjs"])
        run([node, "scripts/deploy.cjs"])
        if occupied(5001):
            ready("Existing IPFS", lambda: bool(request("http://127.0.0.1:5001/api/v0/version", {})["Version"]))
        else:
            ipfs = os.getenv("HEALTHRECORDS_IPFS_BIN") or str(ROOT / ".tools" / ("ipfs.exe" if sys.platform == "win32" else "ipfs"))
            if not (local / "ipfs/config").exists():
                run([ipfs, "init", "--profile=test"])
            run([ipfs, "config", "Addresses.API", "/ip4/127.0.0.1/tcp/5001"])
            # Offline mode keeps demo objects on this machine and avoids public discovery.
            start("ipfs", [ipfs, "daemon", "--offline"])
            ready("IPFS", lambda: bool(request("http://127.0.0.1:5001/api/v0/version", {})["Version"]))
        deployment = json.loads((local / "deployment.json").read_text())
        environment["CONTRACT_ADDRESS"] = deployment["address"]
        origins = {url, f"http://localhost:{args.port}"}
        if environment.get("FRONTEND_ORIGINS"):
            origins.update(environment["FRONTEND_ORIGINS"].split(","))
        if environment.get("HEALTHRECORDS_PUBLIC_URL"):
            origins.add(environment["HEALTHRECORDS_PUBLIC_URL"].rstrip("/"))
        environment["FRONTEND_ORIGINS"] = ",".join(sorted(origins))
        start("site", [python, "-m", "uvicorn", "backend.site:app", "--host", args.host, "--port", str(args.port)])

        def healthy():
            health = request(url + "/api/health")
            return all(health.get(field) for field in ("hardhat_connected", "contract_configured", "encryption_configured", "ipfs_connected"))

        ready("HealthRecords site and API", healthy)
        run([python, "backend/check_ipfs.py"])
        print("All services are healthy. Site is serving on port " + str(args.port) + ".", flush=True)
        print("Use MetaMask test wallets; the deployer wallet is the administrator. See README.md.", flush=True)
        if args.check:
            print("Startup check passed; stopping services created by this check.", flush=True)
            return 0
        if not args.no_browser:
            webbrowser.open(url)
        print("Press Ctrl+C to stop the services started here. Logs: .local/", flush=True)
        while True:
            for name, process in children:
                if process.poll() is not None:
                    raise RuntimeError(f"{name} exited; inspect .local/{name}.log.")
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    finally:
        for name, process in reversed(children):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        for log in logs:
            log.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print("Startup failed: " + str(error), file=sys.stderr)
        raise SystemExit(1)

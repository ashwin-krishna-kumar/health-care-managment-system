"""Repeatable project setup, with verified official Kubo downloads."""
from pathlib import Path
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[1]
KUBO_VERSION = "0.40.0"


def run(args, **kwargs):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True, **kwargs)


def install_ipfs():
    system = {"Linux": "linux", "Darwin": "darwin", "Windows": "windows"}.get(platform.system())
    arch = {"x86_64": "amd64", "AMD64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine())
    if not system or not arch:
        raise RuntimeError("Install Kubo manually on this platform, then set HEALTHRECORDS_IPFS_BIN to its executable.")
    extension = "zip" if system == "windows" else "tar.gz"
    tools = ROOT / ".tools"
    executable = tools / ("ipfs.exe" if system == "windows" else "ipfs")
    version_file = tools / "kubo-version"
    if executable.exists() and version_file.exists() and version_file.read_text().strip() == KUBO_VERSION:
        run([executable, "version"])
        return
    tools.mkdir(exist_ok=True)
    cache = ROOT / ".cache/downloads"
    cache.mkdir(parents=True, exist_ok=True)
    filename = f"kubo_v{KUBO_VERSION}_{system}-{arch}.{extension}"
    url = f"https://github.com/ipfs/kubo/releases/download/v{KUBO_VERSION}/{filename}"
    archive = cache / filename
    print("Downloading official Kubo release and SHA-512 checksum…", flush=True)
    with urllib.request.urlopen(url + ".sha512", timeout=60) as response:
        checksum = response.read().decode().split()[0].lower()
    if len(checksum) != 128 or any(c not in "0123456789abcdef" for c in checksum):
        raise RuntimeError("Invalid checksum from official Kubo release.")
    if not archive.exists() or hashlib.sha512(archive.read_bytes()).hexdigest() != checksum:
        temporary = archive.with_suffix(".download")
        with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
        if hashlib.sha512(temporary.read_bytes()).hexdigest() != checksum:
            temporary.unlink()
            raise RuntimeError("Kubo archive checksum failed. Nothing was installed.")
        temporary.replace(archive)
    member = "kubo/" + executable.name
    if extension == "zip":
        with zipfile.ZipFile(archive) as packed:
            content = packed.read(member)
    else:
        with tarfile.open(archive) as packed:
            content = packed.extractfile(member).read()
    executable.write_bytes(content)
    executable.chmod(0o755)
    version_file.write_text(KUBO_VERSION + "\n")
    run([executable, "version"])


def main():
    os.chdir(ROOT)
    python = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not python.exists():
        venv.create(ROOT / ".venv", with_pip=True)
    lock = ROOT / "backend/requirements.lock"
    dependencies = [python, "-m", "pip", "install", "--quiet", "--cache-dir", ROOT / ".cache/pip", "-r",
                    lock if lock.exists() else ROOT / "backend/requirements.txt"]
    if not lock.exists():
        dependencies.append("pytest")
    run(dependencies)
    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("Install Node.js 22 or 24 (including npm), then rerun setup.")
    run([npm, "ci", "--cache", ROOT / ".cache/npm", "--no-audit", "--no-fund"])
    if not os.getenv("HEALTHRECORDS_IPFS_BIN"):
        install_ipfs()
    run([python, "backend/configure_env.py"])
    run([shutil.which("node"), "scripts/compile.cjs"])
    print("Setup complete. Run npm run dev to start all four components.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print("Setup failed: " + str(error), file=sys.stderr)
        raise SystemExit(1)

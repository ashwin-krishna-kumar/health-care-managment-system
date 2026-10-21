param(
    [string]$ProjectPath = "C:\Users\ashwi\healthcare-blockchain"
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath (Join-Path $ProjectPath "backend\main.py"))) {
    throw "Project backend not found at $ProjectPath. Use -ProjectPath with your project folder."
}
$pythonExecutable = Join-Path $ProjectPath ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    throw "Project Python environment is missing. Run Apply-Update.ps1 after creating the project's .venv."
}
& $pythonExecutable (Join-Path $PSScriptRoot "backend\configure_env.py") --env-file (Join-Path $ProjectPath "backend\.env")
if ($LASTEXITCODE -ne 0) { throw "Configuration needs attention. Read the message above." }
& $pythonExecutable (Join-Path $PSScriptRoot "backend\check_ipfs.py") --env-file (Join-Path $ProjectPath "backend\.env")
if ($LASTEXITCODE -ne 0) { throw "IPFS is not ready. Open IPFS Desktop and run this command again." }
Write-Host ""
Write-Host "Encrypted IPFS storage passed its connection test." -ForegroundColor Green
Write-Host "Restart your backend from your project folder:"
Write-Host '  .\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000'
Write-Host "Then refresh http://127.0.0.1:5500 and confirm IPFS: Connected."

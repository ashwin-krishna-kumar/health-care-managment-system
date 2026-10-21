param(
    [string]$ProjectPath = "C:\Users\ashwi\healthcare-blockchain",
    [switch]$SkipInstall
)
$ErrorActionPreference = "Stop"
$sourceDirectory = $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $ProjectPath "package.json"))) {
    throw "The existing project was not found at $ProjectPath. Use -ProjectPath with its actual folder."
}
$backupName = (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + [Guid]::NewGuid().ToString("N").Substring(0,8)
$backupDirectory = Join-Path $ProjectPath ("healthrecords-backups\" + $backupName)
$filesToCopy = @(
    "frontend\index.html", "frontend\app.js", "frontend\portal.css", "frontend\abi.json",
    "frontend\vendor\ethers.umd.min.js", "frontend\vendor\ethers-LICENSE.md",
    "backend\main.py", "backend\requirements.txt", "backend\configure_env.py", "backend\check_ipfs.py"
)
foreach ($relativePath in $filesToCopy) {
    $sourceFile = Join-Path $sourceDirectory $relativePath
    if (-not (Test-Path -LiteralPath $sourceFile)) { throw "Missing update file: $relativePath. Extract the entire ZIP first." }
}
foreach ($relativePath in $filesToCopy) {
    $destinationFile = Join-Path $ProjectPath $relativePath
    if (Test-Path -LiteralPath $destinationFile) {
        $backupFile = Join-Path $backupDirectory $relativePath
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $backupFile) | Out-Null
        Copy-Item -LiteralPath $destinationFile -Destination $backupFile -Force
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destinationFile) | Out-Null
    Copy-Item -LiteralPath (Join-Path $sourceDirectory $relativePath) -Destination $destinationFile -Force
}
$initFile = Join-Path $ProjectPath "backend\__init__.py"
if (-not (Test-Path -LiteralPath $initFile)) { New-Item -ItemType File -Path $initFile | Out-Null }
$pythonExecutable = Join-Path $ProjectPath ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    throw "Code copied and backed up. Create .venv in your project, then install backend\requirements.txt and run backend\configure_env.py."
}
if (-not $SkipInstall) {
    & $pythonExecutable -m pip install -r (Join-Path $ProjectPath "backend\requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed. Your prior code is backed up at $backupDirectory." }
    & $pythonExecutable (Join-Path $ProjectPath "backend\configure_env.py")
    if ($LASTEXITCODE -ne 0) { throw "Configuration needs attention. Read the message above; your existing .env has been retained." }
}
Write-Host ""
Write-Host "Update copied to $ProjectPath" -ForegroundColor Green
Write-Host "Previous code backup: $backupDirectory"
Write-Host "Keep your existing .env, database, encrypted files, contract deployment and Hardhat files."
Write-Host "Open IPFS Desktop. Restart the backend and frontend using README.md, then open http://127.0.0.1:5500."

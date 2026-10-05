param([switch]$CheckOnly)

$ErrorActionPreference = 'Stop'
$logPath = Join-Path $PSScriptRoot 'launch.log'

function Write-StartupMessage {
    param([string]$Message)
    Write-Host $Message
    Add-Content -LiteralPath $logPath -Value $Message -Encoding UTF8
}

function Write-CommandOutput {
    process {
        $line = [string]$_
        Write-Host $line
        Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
    }
}

try {
    Set-Location -LiteralPath $PSScriptRoot
    Set-Content -LiteralPath $logPath -Value ('TimeIndex startup: ' + (Get-Date -Format o)) -Encoding UTF8

    $uvCommand = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($uvCommand) {
        $uvPath = $uvCommand.Source
    } else {
        $uvPath = $null
        if ($env:USERPROFILE) {
            foreach ($candidate in @(
                (Join-Path $env:USERPROFILE '.local\bin\uv.exe'),
                (Join-Path $env:USERPROFILE '.cargo\bin\uv.exe')
            )) {
                if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                    $uvPath = $candidate
                    break
                }
            }
        }
    }
    if (-not $uvPath) {
        throw 'uv was not found. Reopen Windows after installing uv, then run start.cmd again.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'uv.lock') -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $PSScriptRoot '..\pyproject.toml') -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $PSScriptRoot '..\src\TimeIndex\config.yaml') -PathType Leaf)) {
        throw 'Project files are incomplete. Extract the entire ZIP, including the TimeIndex parent folder.'
    }

    $env:UV_CACHE_DIR = Join-Path $PSScriptRoot '.uv-cache'
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $PSScriptRoot '.python'
    Write-StartupMessage 'Installing or checking the locked environment...'
    $ErrorActionPreference = 'Continue'
    try {
        & $uvPath sync --frozen --python 3.12 --reinstall-package timeindex 2>&1 | Write-CommandOutput
        $syncExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = 'Stop'
    }
    if ($syncExit -ne 0) {
        throw "uv sync failed with exit code $syncExit"
    }
    if ($CheckOnly) {
        Write-StartupMessage 'Environment check passed.'
        exit 0
    }

    Write-StartupMessage 'Open http://127.0.0.1:8501 in a browser. Keep this window open.'
    $ErrorActionPreference = 'Continue'
    try {
        & $uvPath run --no-sync streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true 2>&1 |
            Write-CommandOutput
        $serverExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = 'Stop'
    }
    if ($serverExit -ne 0) {
        throw "Streamlit exited with code $serverExit"
    }
} catch {
    $message = $_.Exception.Message
    Write-Host "Startup failed: $message" -ForegroundColor Red
    Add-Content -LiteralPath $logPath -Value ("Startup failed: $message") -Encoding UTF8
    exit 1
}


$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$platform = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$repository = (Resolve-Path -LiteralPath (Join-Path $platform '..')).Path
$repositoryPrefix = $repository.TrimEnd('\') + '\'
$outputDirectory = Join-Path $platform 'dist'
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
$archivePath = Join-Path $outputDirectory ('TimeIndex-test-platform-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.zip')

$rootFiles = @('pyproject.toml', 'uv.lock', '.python-version', 'README.md', 'README_zh.md', 'LICENSE')
$platformFiles = @('app.py', 'launch.ps1', 'start.cmd', 'package.ps1', 'pyproject.toml', 'uv.lock', 'README.md', 'paper_experiment.md', 'plan.md', '.gitignore')
$platformDirectories = @('datasets', 'platform_core', 'tests', 'openspec')
$skipDirectories = @('.lancedb', '__pycache__', '.git', '.venv', '.python', '.uv-cache', 'runs', '.uploads', 'dist')
$hashes = @{}
$totalBytes = [long]0

function Add-PackageFile {
    param([string]$FilePath)
    $absolute = [System.IO.Path]::GetFullPath($FilePath)
    if (-not $absolute.StartsWith($repositoryPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "File is outside TimeIndex: $absolute"
    }
    $entryName = 'TimeIndex/' + $absolute.Substring($repositoryPrefix.Length).Replace('\', '/')
    if ($hashes.ContainsKey($entryName)) {
        throw "Duplicate archive entry: $entryName"
    }
    $item = Get-Item -LiteralPath $absolute
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing a linked file: $absolute"
    }
    [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
        $archive, $absolute, $entryName, [System.IO.Compression.CompressionLevel]::Optimal)
    $hashes[$entryName] = (Get-FileHash -LiteralPath $absolute -Algorithm SHA256).Hash
    $script:totalBytes += $item.Length
}

function Add-PackageTree {
    param([string]$Directory, [string[]]$AllowedExtensions)
    foreach ($item in Get-ChildItem -LiteralPath $Directory -Force) {
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing a linked item: $($item.FullName)"
        }
        if ($item.PSIsContainer) {
            if ($skipDirectories -notcontains $item.Name) {
                Add-PackageTree -Directory $item.FullName -AllowedExtensions $AllowedExtensions
            }
        } elseif ($AllowedExtensions -contains $item.Extension) {
            Add-PackageFile -FilePath $item.FullName
        }
    }
}

foreach ($name in $rootFiles) {
    $path = Join-Path $repository $name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Missing required project file: $path"
    }
}
foreach ($name in $platformFiles) {
    $path = Join-Path $platform $name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Missing required platform file: $path"
    }
}
foreach ($name in $platformDirectories) {
    $path = Join-Path $platform $name
    if (-not (Test-Path -LiteralPath $path -PathType Container)) {
        throw "Missing required platform directory: $path"
    }
}

$archive = [System.IO.Compression.ZipFile]::Open($archivePath, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($name in $rootFiles) { Add-PackageFile -FilePath (Join-Path $repository $name) }
    Add-PackageTree -Directory (Join-Path $repository 'src\TimeIndex') -AllowedExtensions @('.py', '.yaml', '.ps1', '.exe')
    foreach ($name in $platformFiles) { Add-PackageFile -FilePath (Join-Path $platform $name) }
    Add-PackageTree -Directory (Join-Path $platform 'datasets') -AllowedExtensions @('.json', '.jsonl')
    Add-PackageTree -Directory (Join-Path $platform 'platform_core') -AllowedExtensions @('.py')
    Add-PackageTree -Directory (Join-Path $platform 'tests') -AllowedExtensions @('.py')
    Add-PackageTree -Directory (Join-Path $platform 'openspec') -AllowedExtensions @('.md', '.yaml', '.json')
} finally {
    $archive.Dispose()
}

$reader = [System.IO.Compression.ZipFile]::OpenRead($archivePath)
try {
    if ($reader.Entries.Count -ne $hashes.Count) { throw 'Archive entry count mismatch' }
    foreach ($entry in $reader.Entries) {
        $stream = $entry.Open()
        $sha256 = [System.Security.Cryptography.SHA256]::Create()
        try {
            $actual = [System.BitConverter]::ToString($sha256.ComputeHash($stream)).Replace('-', '')
            if ($actual -ne $hashes[$entry.FullName]) {
                throw "Archive verification failed: $($entry.FullName)"
            }
        } finally {
            $sha256.Dispose()
            $stream.Dispose()
        }
    }
} finally {
    $reader.Dispose()
}

$file = Get-Item -LiteralPath $archivePath
$digest = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash
Write-Host "Package: $archivePath"
Write-Host "Files: $($hashes.Count); source size: $([math]::Round($totalBytes / 1MB, 2)) MiB; ZIP size: $([math]::Round($file.Length / 1MB, 2)) MiB"
Write-Host "SHA256: $digest"

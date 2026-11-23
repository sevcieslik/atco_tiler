# ATCO-owned Miniforge bootstrap. Windows PowerShell 5.1; no admin rights needed.
# Pinned release ensures the same Conda/libmamba across operator machines.
$ErrorActionPreference = 'Stop'
$version = '26.9.4-2'
$asset = "Miniforge3-$version-Windows-x86_64.exe"
$baseUrl = "https://github.com/conda-forge/miniforge/releases/download/$version"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$cache = Join-Path $env:LOCALAPPDATA 'ATCO\installers'
$prefix = Join-Path $env:LOCALAPPDATA 'ATCO\miniforge3'
$conda = Join-Path $prefix 'condabin\conda.bat'
$record = Join-Path $root 'config\conda_path.txt'

function Check-Conda {
    if (-not (Test-Path -LiteralPath $conda -PathType Leaf)) { return $false }
    # Avoid running a Conda version without the libmamba solver.
    $exe = Join-Path $prefix 'Scripts\conda.exe'
    if (-not (Test-Path -LiteralPath $exe)) { return $false }
    $out = & $exe --version 2>&1
    if ($LASTEXITCODE -ne 0) { return $false }
    if ("$out" -notmatch 'conda (\d+)\.(\d+)') { return $false }
    $major = [int]$Matches[1]
    if ($major -lt 24) { throw "ATCO-managed Conda is too old ($out). Existing installation left untouched: $prefix" }
    Write-Host "ATCO-managed Conda ready: $out ($prefix)"
    return $true
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $record) | Out-Null
if (-not (Check-Conda)) {
    if (Test-Path -LiteralPath $prefix) {
        throw "Incomplete ATCO-managed Miniforge at $prefix. Refusing to overwrite/remove it. Inspect before retrying."
    }
    New-Item -ItemType Directory -Force -Path $cache | Out-Null
    $installer = Join-Path $cache $asset
    $checksumFile = "$installer.sha256"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Write-Host "Downloading pinned Miniforge $version from conda-forge (first run only)..."
    Invoke-WebRequest -Uri "$baseUrl/$asset.sha256" -OutFile $checksumFile -MaximumRedirection 10 -UseBasicParsing
    if (-not (Test-Path -LiteralPath $installer)) {
        Invoke-WebRequest -Uri "$baseUrl/$asset" -OutFile $installer -MaximumRedirection 10 -UseBasicParsing
    }
    $checksumText = [IO.File]::ReadAllText($checksumFile)
    if ($checksumText -notmatch '(?i)\b([0-9a-f]{64})\b') { throw 'Invalid official Miniforge SHA256 checksum file.' }
    $expected = $Matches[1].ToLowerInvariant()
    $actual = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { throw "Miniforge SHA256 mismatch: $installer. Installation not started." }
    Write-Host "Official Miniforge SHA256 verified. Installing for current user without PATH/registry changes..."
    $args = @('/InstallationType=JustMe', '/RegisterPython=0', '/AddToPath=0',
              '/NoRegistry=1', '/NoShortcuts=1', '/S', "/D=$prefix")
    $proc = Start-Process -FilePath $installer -ArgumentList $args -Wait -PassThru
    if ($proc.ExitCode -ne 0) { throw "Miniforge installer exited with code $($proc.ExitCode)." }
    if (-not (Check-Conda)) { throw "Miniforge installation finished, but Conda validation failed: $prefix" }
}
Set-Content -LiteralPath $record -Value $conda -Encoding ASCII
Write-Host "ATCO_CONDA_BAT=$conda"

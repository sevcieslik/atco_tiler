# Bootstrap standalone Micromamba for ATCO; no system Conda required.
# Runs on Windows PowerShell 5.1, logs through INSTALL_RUNTIME logger.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$installDir = Join-Path $root ".installer"
$exe = Join-Path $installDir "micromamba.exe"
$uri = "https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-win-64"
try {
    if (-not (Test-Path $exe)) {
        New-Item -ItemType Directory -Path $installDir -Force | Out-Null
        Write-Host "Downloading ATCO standalone Micromamba from official mamba-org release..."
        $tmp = Join-Path $installDir ("micromamba_" + [Guid]::NewGuid().ToString("N") + ".exe")
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri $uri -UseBasicParsing -OutFile $tmp -MaximumRedirection 10
            if ((Get-Item $tmp).Length -lt 1024000) {
                throw "Download too small to be a valid Micromamba executable."
            }
            Move-Item -Path $tmp -Destination $exe -Force
        }
        finally {
            if (Test-Path $tmp) { Remove-Item $tmp -Force }
        }
    }
    & $exe --version
    if ($LASTEXITCODE -ne 0) { throw "Micromamba version check failed (exit $LASTEXITCODE)." }
    Write-Host "ATCO Micromamba ready: $exe"
    exit 0
}
catch {
    Write-Error ("Unable to initialise ATCO Micromamba: " + $_.Exception.Message +
        ". Check internet access to github.com/mamba-org/micromamba-releases. " +
        "Existing Conda and ATCO runtime were not changed.")
    exit 1
}

# ATCO Retiler installation logger. Requires only Windows PowerShell 5.1.
# The complete Conda console output is mirrored to a timestamped local file.
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$log = Join-Path $logDir "install_runtime_$stamp.log"
$core = Join-Path $root 'INSTALL_RUNTIME_CORE.bat'
$started = Get-Date
# New-Object cannot reliably bind the nested constructor expression in
# Windows PowerShell 5.1; use explicit .NET constructor calls instead.
$writer = $null
try {
    $encoding = [System.Text.UTF8Encoding]::new($false)
    $writer = [System.IO.StreamWriter]::new($log, $false, $encoding)
    $writer.AutoFlush = $true
}
catch {
    Write-Warning "Could not open installation log '$log': $($_.Exception.Message). Continuing without file logging."
}
function Log-Line([string]$line) {
    $entry = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff')] $line"
    Write-Host $entry
    if ($null -ne $script:writer) {
        try { $script:writer.WriteLine($entry) }
        catch {
            Write-Warning "Installation log write failed: $($_.Exception.Message). Continuing installer."
            try { $script:writer.Dispose() } catch {}
            $script:writer = $null
        }
    }
}
$rc = 1
try {
    Log-Line 'ATCO v1.08 runtime installation started'
    Log-Line "Computer: $env:COMPUTERNAME | User: $env:USERNAME | PowerShell: $($PSVersionTable.PSVersion)"
    Log-Line "Project folder: $(Split-Path -Parent $root)"
    Log-Line "Environment: $(Join-Path $root '.venv')"
    Log-Line "Log: $log"
    if (-not (Test-Path $core)) { throw "Missing installer: $core" }
    # cmd /d /c runs the original Conda installer with its native exit code.
    # Forward its full stdout/stderr to the console and the durable log.
    & cmd.exe /d /c ('"' + $core + '"') 2>&1 | ForEach-Object {
        Log-Line ([string]$_)
    }
    $rc = $LASTEXITCODE
    if ($null -eq $rc) { $rc = 1 }
}
catch {
    Log-Line "ERROR: $($_.Exception.Message)"
    $rc = 1
}
finally {
    $seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 1)
    Log-Line "ATCO runtime installation finished | exit_code=$rc | elapsed_seconds=$seconds"
    if ($null -ne $writer) {
        $writer.Dispose()
        Write-Host "Installation log saved: $log"
    } else {
        Write-Warning "Installation completed without a file log. See console output."
    }
}
exit $rc

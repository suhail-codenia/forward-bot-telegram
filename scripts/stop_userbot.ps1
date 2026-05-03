# Stops pythonw.exe processes whose command line includes this project's userbot.py.
$scriptRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$userbotPath = (Join-Path $scriptRoot "userbot.py").ToLowerInvariant()
$userbotEscaped = [regex]::Escape($userbotPath)

$targets = Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
        if (-not $_.CommandLine) { return $false }
        return $_.CommandLine.ToLowerInvariant().Contains($userbotPath)
    }

if (-not $targets) {
    Write-Host "[INFO] No running userbot (pythonw with userbot.py) found."
    exit 0
}

foreach ($p in $targets) {
    try {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
        Write-Host "[OK] Stopped PID $($p.ProcessId)"
    }
    catch {
        Write-Host "[WARN] Could not stop PID $($p.ProcessId): $_"
    }
}

exit 0

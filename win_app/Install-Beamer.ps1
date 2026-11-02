$ErrorActionPreference = 'Stop'

# Over SSH there is nobody to press Enter and no desktop to open a window on: Read-Host fails on
# the null stdin, and a Start-Process lands Beamer in session 0, where it is an invisible,
# tray-less receiver that also holds the SSH pipe open so the install never returns.
$Interactive = [Environment]::UserInteractive -and -not $env:SSH_CLIENT

trap {
    Write-Host $_ -ForegroundColor Red
    if ($Interactive) { Read-Host 'Press Enter to close' }
    break
}

$PowerShellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$MaintenanceLauncher = Join-Path $PSScriptRoot 'Invoke-InstallMaintenance.ps1'
& $PowerShellPath -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $MaintenanceLauncher -CheckIdentityOnly
if ($LASTEXITCODE -ne 0) { throw 'The installer must run for the signed-in user.' }

$InstallDir = Join-Path $env:LOCALAPPDATA 'Beamer'
$ExeSource = Join-Path $PSScriptRoot 'dist\Beamer.exe'
$ExeDestination = Join-Path $InstallDir 'Beamer.exe'

& "$PSScriptRoot\build_win_app.ps1"
if (-not (Test-Path $ExeSource)) { throw 'The Windows build did not produce Beamer.exe' }

New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
$InstalledProcesses = @(Get-Process 'Beamer' -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $ExeDestination })
$InstalledProcesses | Stop-Process -Force
$InstalledProcesses | Wait-Process -Timeout 10 -ErrorAction SilentlyContinue
# The exe can stay locked for a moment after the process has gone: the copy can fail with
# "being used by another process" straight after Wait-Process returns, and succeed on a rerun.
for ($attempt = 1; ; $attempt++) {
    try {
        Copy-Item -Path $ExeSource -Destination $ExeDestination -Force -ErrorAction Stop
        break
    } catch [System.IO.IOException] {
        if ($attempt -ge 10) { throw }
        Start-Sleep -Milliseconds 500
    }
}

$Shell = New-Object -ComObject WScript.Shell
$StartMenuPath = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Beamer.lnk'
$StartMenuShortcut = $Shell.CreateShortcut($StartMenuPath)
$StartMenuShortcut.TargetPath = $ExeDestination
$StartMenuShortcut.WorkingDirectory = $InstallDir
$StartMenuShortcut.Description = 'Beamer Windows Receiver'
$StartMenuShortcut.Save()

# No installer writes a desktop shortcut; delete any this or an older install left.
Remove-Item -Path (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Beamer.lnk') -Force -ErrorAction SilentlyContinue

# Remove pre-task startup entries so there is only one starter.
$StartupPath = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\Beamer.lnk'
Remove-Item -Path $StartupPath -Force -ErrorAction SilentlyContinue

# Defensively remove a legacy Run-key entry too, in case an older install used one instead.
Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'Beamer' -ErrorAction SilentlyContinue

& $PowerShellPath -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $MaintenanceLauncher -ExePath $ExeDestination
if ($LASTEXITCODE -ne 0) { throw 'Beamer was installed, but administrator maintenance did not complete.' }

if ($Interactive) {
    Write-Host "Beamer installed at $ExeDestination" -ForegroundColor Green
    Read-Host 'Press Enter to close'
} else {
    Write-Host "Beamer installed at $ExeDestination and not started; start it at the PC." -ForegroundColor Green
}

[CmdletBinding()]
param([string]$ExePath, [switch]$CheckIdentityOnly, [switch]$Remove)

$ErrorActionPreference = 'Stop'
try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    $UserSid = $identity.User.Value
    $IsElevated = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if ($IsElevated) {
        $sessionId = (Get-Process -Id $PID).SessionId
        foreach ($shell in @(Get-CimInstance -ClassName Win32_Process -Filter "Name='explorer.exe' AND SessionId=$sessionId" -ErrorAction Stop)) {
            $owner = Invoke-CimMethod -InputObject $shell -MethodName GetOwnerSid -ErrorAction Stop
            if ($owner.ReturnValue -ne 0 -or $owner.Sid -ne $UserSid) {
                throw 'Beamer must be installed by the signed-in user. Run the installer normally; give administrator permission only when maintenance asks.'
            }
        }
    }
    if ($CheckIdentityOnly) { exit 0 }
    $MaintenancePath = Join-Path $PSScriptRoot 'Install-Maintenance.ps1'
    $PowerShellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    # -File passes literal arguments, including apostrophes and shell metacharacters in profile paths.
    # These paths end in fixed filenames, so Windows command-line quoting cannot swallow a closing quote.
    if ($ExePath.Contains('"') -or $MaintenancePath.Contains('"')) { throw 'Invalid installer path.' }
    $arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -ExePath "{1}" -UserSid "{2}"' -f $MaintenancePath, $ExePath, $UserSid
    if ($Remove) { $arguments += ' -Remove' }
    if ($IsElevated) {
        $process = Start-Process -FilePath $PowerShellPath -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru
    } else {
        $process = Start-Process -FilePath $PowerShellPath -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -Wait -PassThru
    }
    if ($process.ExitCode -ne 0) { throw "Installer maintenance failed (exit code $($process.ExitCode))." }
    exit 0
} catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
}

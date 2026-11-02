param(
    [Parameter(Mandatory = $true)][string]$Zip,
    [string]$AcceptanceDirectory,
    [ValidateRange(10, 60)][int]$TimeoutSeconds = 45
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectDir = Split-Path -Parent $ScriptDir
if (-not $AcceptanceDirectory) { $AcceptanceDirectory = Join-Path $ProjectDir 'win_app\dist\portable\qualification' }
$AcceptanceDirectory = [IO.Path]::GetFullPath($AcceptanceDirectory)
if (Test-Path $AcceptanceDirectory) { throw 'Use a fresh acceptance directory; existing test state is never removed' }
$Zip = (Resolve-Path $Zip).Path
New-Item -ItemType Directory -Path $AcceptanceDirectory | Out-Null
$Extracted = Join-Path $AcceptanceDirectory 'extracted'
Expand-Archive -Path $Zip -DestinationPath $Extracted
$Payload = Join-Path $Extracted 'Beamer-portable'
$Executable = Join-Path $Payload 'Beamer.exe'
if (-not (Test-Path $Executable) -or -not (Test-Path (Join-Path $Payload 'Beamer.portable'))) { throw 'The zip is not a Beamer portable payload' }
if (Test-Path (Join-Path $Payload 'data')) { throw 'The zip contains user data; qualification requires an empty payload' }
Copy-Item (Join-Path $ScriptDir 'portable_acceptance.py') (Join-Path $Payload 'portable_acceptance.py')
New-Item -ItemType Directory -Path (Join-Path $Payload 'data') | Out-Null
$ProfileRoot = Join-Path $AcceptanceDirectory 'host-profile'
$LocalProfile = Join-Path $ProfileRoot 'local'
$RoamingProfile = Join-Path $ProfileRoot 'roaming'
$TemporaryProfile = Join-Path $ProfileRoot 'temp'
New-Item -ItemType Directory -Path $LocalProfile, $RoamingProfile, $TemporaryProfile | Out-Null

# SSH administrator logons need not have a linked token. Restrict only our own token.
# Keep the parent environment untouched: ShellExecute creates profile caches before app startup.
# https://learn.microsoft.com/en-us/windows/win32/secauthz/restricted-tokens
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class PortableQualificationToken {
    [StructLayout(LayoutKind.Sequential)]
    struct SidAndAttributes { public IntPtr sid; public uint attributes; }
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct StartupInfo {
        public int cb; public string reserved, desktop, title;
        public int x, y, width, height, xChars, yChars, fill, flags;
        public short show, reservedSize; public IntPtr reservedData, input, output, error;
    }
    [StructLayout(LayoutKind.Sequential)]
    struct ProcessInfo { public IntPtr process, thread; public int pid, tid; }
    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool CreateRestrictedToken(IntPtr token, uint flags, uint disableCount,
        IntPtr disable, uint deleteCount, IntPtr delete, uint restrictCount, IntPtr restrict,
        out IntPtr restricted);
    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool GetTokenInformation(IntPtr token, int kind, IntPtr value, int size, out int needed);
    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool SetTokenInformation(IntPtr token, int kind, IntPtr value, int size);
    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool ConvertStringSidToSidW(string value, out IntPtr sid);
    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool ConvertSidToStringSidW(IntPtr sid, out IntPtr value);
    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool ConvertStringSecurityDescriptorToSecurityDescriptorW(string value,
        uint revision, out IntPtr descriptor, IntPtr size);
    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool GetSecurityDescriptorDacl(IntPtr descriptor, out bool present,
        out IntPtr acl, out bool defaulted);
    [DllImport("advapi32.dll")] static extern uint GetLengthSid(IntPtr sid);
    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool CreateProcessAsUserW(IntPtr token, string application,
        StringBuilder command, IntPtr processAttributes, IntPtr threadAttributes,
        bool inheritHandles, uint flags, IntPtr environment, string directory,
        ref StartupInfo startup, out ProcessInfo process);
    [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr pointer);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr handle, uint timeout);
    [DllImport("kernel32.dll")] static extern bool GetExitCodeProcess(IntPtr handle, out uint code);
    [DllImport("kernel32.dll")] static extern bool TerminateProcess(IntPtr handle, uint code);
    public static int RunRestricted(string exe, string arguments, string directory,
        string[] environmentPairs, int timeout, out int launchError, out string launchStage) {
        launchError = 0;
        launchStage = "open-current-token";
        IntPtr token = IntPtr.Zero, restricted = IntPtr.Zero, user = IntPtr.Zero,
            owner = IntPtr.Zero, stringSid = IntPtr.Zero, descriptor = IntPtr.Zero,
            dacl = IntPtr.Zero, mediumSid = IntPtr.Zero, label = IntPtr.Zero,
            environment = IntPtr.Zero;
        ProcessInfo child = new ProcessInfo();
        try {
            if (!OpenProcessToken(GetCurrentProcess(), 0x8B, out token)) {
                launchError = Marshal.GetLastWin32Error(); return -1;
            }
            launchStage = "create-restricted-token";
            if (!CreateRestrictedToken(token, 0x1 | 0x4, 0, IntPtr.Zero,
                0, IntPtr.Zero, 0, IntPtr.Zero, out restricted)) {
                launchError = Marshal.GetLastWin32Error(); return -1;
            }
            launchStage = "query-user";
            int needed;
            GetTokenInformation(restricted, 1, IntPtr.Zero, 0, out needed);
            if (needed <= 0) { launchError = Marshal.GetLastWin32Error(); return -1; }
            user = Marshal.AllocHGlobal(needed);
            if (!GetTokenInformation(restricted, 1, user, needed, out needed)) {
                launchError = Marshal.GetLastWin32Error(); return -1;
            }
            IntPtr userSid = Marshal.ReadIntPtr(user);
            // LUA makes Administrators deny-only; new objects must remain accessible to this user.
            launchStage = "set-owner";
            owner = Marshal.AllocHGlobal(IntPtr.Size);
            Marshal.WriteIntPtr(owner, userSid);
            if (!SetTokenInformation(restricted, 4, owner, IntPtr.Size)) {
                launchError = Marshal.GetLastWin32Error(); return -1;
            }
            launchStage = "set-default-dacl";
            if (!ConvertSidToStringSidW(userSid, out stringSid)) {
                launchError = Marshal.GetLastWin32Error(); return -1;
            }
            string sddl = "D:(A;;GA;;;" + Marshal.PtrToStringUni(stringSid) + ")(A;;GA;;;SY)";
            if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1,
                out descriptor, IntPtr.Zero)) {
                launchError = Marshal.GetLastWin32Error(); return -1;
            }
            bool present, defaulted;
            IntPtr acl;
            if (!GetSecurityDescriptorDacl(descriptor, out present, out acl, out defaulted)) {
                launchError = Marshal.GetLastWin32Error(); return -1;
            }
            if (!present || acl == IntPtr.Zero) { launchError = 1336; return -1; }
            dacl = Marshal.AllocHGlobal(IntPtr.Size);
            Marshal.WriteIntPtr(dacl, acl);
            if (!SetTokenInformation(restricted, 6, dacl, IntPtr.Size)) {
                launchError = Marshal.GetLastWin32Error(); return -1;
            }
            launchStage = "set-integrity-medium";
            if (!ConvertStringSidToSidW("S-1-16-8192", out mediumSid)) {
                launchError = Marshal.GetLastWin32Error(); return -1;
            }
            int labelSize = Marshal.SizeOf(typeof(SidAndAttributes));
            label = Marshal.AllocHGlobal(labelSize);
            Marshal.WriteIntPtr(label, mediumSid);
            Marshal.WriteInt32(label, IntPtr.Size, 0x20);
            if (!SetTokenInformation(restricted, 25, label, labelSize + (int)GetLengthSid(mediumSid))) {
                launchError = Marshal.GetLastWin32Error(); return -1;
            }
            launchStage = "build-environment";
            var pairs = new List<string>(environmentPairs);
            pairs.Sort(StringComparer.OrdinalIgnoreCase);
            environment = Marshal.StringToHGlobalUni(string.Join("\0", pairs.ToArray()) + "\0\0");
            StartupInfo startup = new StartupInfo(); startup.cb = Marshal.SizeOf(startup);
            StringBuilder command = new StringBuilder("\"" + exe + "\" " + arguments);
            launchStage = "create-process-as-user";
            if (!CreateProcessAsUserW(restricted, exe, command, IntPtr.Zero, IntPtr.Zero,
                false, 0x400 | 0x08000000, environment, directory, ref startup, out child)) {
                launchError = Marshal.GetLastWin32Error(); return -1;
            }
            launchStage = "launched";
            CloseHandle(child.thread); child.thread = IntPtr.Zero;
            if (WaitForSingleObject(child.process, (uint)timeout) != 0) {
                TerminateProcess(child.process, 124);
                WaitForSingleObject(child.process, 5000);
                return 124;
            }
            uint code;
            return GetExitCodeProcess(child.process, out code) ? (int)code : 125;
        } finally {
            if (child.thread != IntPtr.Zero) CloseHandle(child.thread);
            if (child.process != IntPtr.Zero) CloseHandle(child.process);
            if (environment != IntPtr.Zero) Marshal.FreeHGlobal(environment);
            if (label != IntPtr.Zero) Marshal.FreeHGlobal(label);
            if (mediumSid != IntPtr.Zero) LocalFree(mediumSid);
            if (dacl != IntPtr.Zero) Marshal.FreeHGlobal(dacl);
            if (descriptor != IntPtr.Zero) LocalFree(descriptor);
            if (stringSid != IntPtr.Zero) LocalFree(stringSid);
            if (owner != IntPtr.Zero) Marshal.FreeHGlobal(owner);
            if (user != IntPtr.Zero) Marshal.FreeHGlobal(user);
            if (restricted != IntPtr.Zero) CloseHandle(restricted);
            if (token != IntPtr.Zero) CloseHandle(token);
        }
    }
}
'@

function Get-TextHash([string]$Text) {
    $Sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($Sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text)))).Replace('-', '').ToLowerInvariant() }
    finally { $Sha.Dispose() }
}

function Get-SharedFileHash([string]$Path) {
    $Share = [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
    $Stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, $Share)
    $Sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($Sha.ComputeHash($Stream))).Replace('-', '').ToLowerInvariant() }
    finally { $Sha.Dispose(); $Stream.Dispose() }
}

function Get-HostSnapshot {
    $Items = @{}
    $Errors = [Collections.Generic.List[string]]::new()
    foreach ($Entry in @(@('synthetic-localappdata', $LocalProfile), @('synthetic-appdata', $RoamingProfile))) {
        $Label, $Directory = $Entry
        try {
            if (-not (Test-Path $Directory -ErrorAction Stop)) { $Items["directory:$Label"] = 'absent'; continue }
            $Items["directory:$Label"] = 'present'
            foreach ($File in Get-ChildItem $Directory -Force -Recurse -ErrorAction Stop) {
                $Relative = $File.FullName.Substring($Directory.Length).TrimStart('\')
                $Items["file:$Label/$Relative"] = if ($File.PSIsContainer) { 'directory' } else { "$($File.Length):$(Get-SharedFileHash $File.FullName)" }
            }
        } catch { $Errors.Add("Synthetic profile snapshot unavailable ($Label): " + $_.Exception.GetType().Name) }
    }
    try {
        Get-ChildItem $TemporaryProfile -Force -ErrorAction Stop | ForEach-Object {
            $Length = if ($_.PSIsContainer) { 'directory' } else { $_.Length }
            $Items["temp:$($_.Name)"] = "$Length`:$($_.LastWriteTimeUtc.Ticks)"
        }
    } catch { $Errors.Add('Synthetic TEMP immediate-entry snapshot unavailable: ' + $_.Exception.GetType().Name) }
    $RunKey = $null
    try {
        $RunKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Software\Microsoft\Windows\CurrentVersion\Run', $false)
        if ($null -eq $RunKey -or $RunKey.GetValueNames() -notcontains 'Beamer') {
            $Items['registry:run-beamer'] = 'absent'
        } else {
            $RunValue = @($RunKey.GetValueKind('Beamer').ToString(), $RunKey.GetValue('Beamer', $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames))
            $Items['registry:run-beamer'] = Get-TextHash (ConvertTo-Json -InputObject $RunValue -Compress)
        }
    } catch { $Errors.Add('Run registry snapshot unavailable: ' + $_.Exception.GetType().Name) }
    finally { if ($null -ne $RunKey) { $RunKey.Dispose() } }
    foreach ($Entry in @(@('HKCU', [Microsoft.Win32.Registry]::CurrentUser, 'Software\Microsoft\Windows\CurrentVersion\Uninstall'),
                         @('HKLM', [Microsoft.Win32.Registry]::LocalMachine, 'Software\Microsoft\Windows\CurrentVersion\Uninstall'),
                         @('HKLM', [Microsoft.Win32.Registry]::LocalMachine, 'Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'))) {
        $Label, $Hive, $Path = $Entry
        $Base = $null
        try {
            $Base = $Hive.OpenSubKey($Path, $false)
            if ($null -eq $Base) { continue }
            foreach ($Name in $Base.GetSubKeyNames()) {
                $Key = $null
                try {
                    $Key = $Base.OpenSubKey($Name, $false)
                    if ($null -eq $Key) { throw 'Uninstall key disappeared during snapshot' }
                    if ($Key.GetValue('DisplayName') -like '*Beamer*' -or $Name -like '*Beamer*') {
                        $Values = [ordered]@{}
                        foreach ($ValueName in ($Key.GetValueNames() | Sort-Object)) {
                            $Values[$ValueName] = @($Key.GetValueKind($ValueName).ToString(), $Key.GetValue($ValueName, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames))
                        }
                        $Items["registry:uninstall:$Label/$Path/$Name"] = Get-TextHash (ConvertTo-Json -InputObject $Values -Depth 5 -Compress)
                    }
                } finally { if ($null -ne $Key) { $Key.Dispose() } }
            }
        } catch { $Errors.Add("Uninstall registry snapshot unavailable ($Label/$Path): " + $_.Exception.GetType().Name) }
        finally { if ($null -ne $Base) { $Base.Dispose() } }
    }
    try {
        foreach ($Task in Get-ScheduledTask -ErrorAction Stop | Where-Object TaskName -like '*Beamer*') {
            $Xml = Export-ScheduledTask -TaskName $Task.TaskName -TaskPath $Task.TaskPath -ErrorAction Stop
            $Items["task:$($Task.TaskPath)$($Task.TaskName)"] = "$($Task.State):$(Get-TextHash $Xml)"
        }
    } catch { $Errors.Add('Scheduled-task snapshot unavailable: ' + $_.Exception.GetType().Name) }
    try {
        foreach ($Rule in Get-NetFirewallRule -ErrorAction Stop | Where-Object DisplayName -like '*Beamer*') {
            $State = @($Rule | Select-Object Name, DisplayName, Enabled, Direction, Action, Profile;
                       $Rule | Get-NetFirewallApplicationFilter -ErrorAction Stop | Select-Object Program;
                       $Rule | Get-NetFirewallPortFilter -ErrorAction Stop | Select-Object Protocol, LocalPort, RemotePort)
            $Items["firewall:$($Rule.Name)"] = Get-TextHash ($State | ConvertTo-Json -Depth 5 -Compress)
        }
    } catch { $Errors.Add('Firewall snapshot unavailable: ' + $_.Exception.GetType().Name) }
    try {
        foreach ($Service in Get-Service -ErrorAction Stop | Where-Object { $_.Name -like '*Beamer*' -or $_.DisplayName -like '*Beamer*' }) {
            $Items["service:$($Service.Name)"] = [string]$Service.Status
        }
    } catch { $Errors.Add('Service snapshot unavailable: ' + $_.Exception.GetType().Name) }
    return @{ items = $Items; errors = $Errors.ToArray() }
}

$Before = Get-HostSnapshot
$ChildEnvironment = @{
    QT_QPA_PLATFORM = 'offscreen'
    BEAMER_PORTABLE_ACCEPTANCE = '1'
    LOCALAPPDATA = $LocalProfile
    APPDATA = $RoamingProfile
    TEMP = $TemporaryProfile
    TMP = $TemporaryProfile
}
$EnvironmentPairs = [Collections.Generic.List[string]]::new()
foreach ($Entry in [Environment]::GetEnvironmentVariables('Process').GetEnumerator()) {
    if (-not $ChildEnvironment.ContainsKey([string]$Entry.Key)) {
        $EnvironmentPairs.Add("$($Entry.Key)=$($Entry.Value)")
    }
}
foreach ($Name in $ChildEnvironment.Keys) { $EnvironmentPairs.Add("$Name=$($ChildEnvironment[$Name])") }
$ImportReport = Join-Path $Payload 'data\imports.txt'
$Arguments = "--import-probe `"$ImportReport`" portable_acceptance"
$Mode = 'self-restricted-token-create-process-as-user'
$LaunchError = 0
$LaunchStage = 'not-attempted'
$ProbeErrors = [Collections.Generic.List[string]]::new()
$ExitCode = 125
try {
    $ExitCode = [PortableQualificationToken]::RunRestricted($Executable, $Arguments, $Payload,
        $EnvironmentPairs.ToArray(), $TimeoutSeconds * 1000, [ref]$LaunchError, [ref]$LaunchStage)
    if ($ExitCode -eq -1) {
        $ProbeErrors.Add("Restricted probe launch failed at $LaunchStage (Win32 $LaunchError)")
    }
} catch { $ProbeErrors.Add('Probe launch/wait unavailable: ' + $_.Exception.GetType().Name) }
$After = Get-HostSnapshot
$Keys = @($Before.items.Keys) + @($After.items.Keys) | Sort-Object -Unique
$Changes = @($Keys | Where-Object { $Before.items[$_] -ne $After.items[$_] })
$AcceptancePath = Join-Path $Payload 'data\acceptance.json'
$Acceptance = $null
try {
    if (Test-Path $AcceptancePath) { $Acceptance = Get-Content $AcceptancePath -Raw | ConvertFrom-Json }
} catch { $ProbeErrors.Add('Probe report unreadable: ' + $_.Exception.GetType().Name) }
$ImportOk = (Test-Path $ImportReport) -and ((Get-Content $ImportReport -Raw) -match '^ok portable_acceptance ')
$Functional = $ExitCode -eq 0 -and $ImportOk -and $Acceptance -and $Acceptance.passed
$Medium = $Acceptance -and $Acceptance.medium_token
$SnapshotsComplete = $Before.errors.Count -eq 0 -and $After.errors.Count -eq 0
$OutsideUnchanged = $SnapshotsComplete -and $Changes.Count -eq 0
$Complete = $Functional -and $Medium -and $OutsideUnchanged -and $ProbeErrors.Count -eq 0
$Report = [ordered]@{
    status = $(if ($Complete) { 'DONE' } else { 'BLOCKED' })
    functional_passed = [bool]$Functional
    medium_token_proven = [bool]$Medium
    host_snapshot_complete = $SnapshotsComplete
    outside_unchanged = $OutsideUnchanged
    process_exit_code = $ExitCode
    launch_mode = $Mode
    launch_error = $LaunchError
    launch_api = 'CreateProcessAsUserW'
    launch_stage = $LaunchStage
    archive_sha256 = (Get-FileHash $Zip -Algorithm SHA256).Hash.ToLowerInvariant()
    changed_host_entries = $Changes
    snapshot_errors = @($Before.errors) + @($After.errors)
    probe_errors = $ProbeErrors.ToArray()
    snapshot_scope = @('Controlled synthetic LOCALAPPDATA/APPDATA file hashes outside payload', 'Controlled synthetic TEMP immediate-entry metadata outside payload', 'Actual Beamer Run/uninstall registry values', 'Actual Beamer scheduled tasks', 'Actual Beamer firewall rules', 'Actual Beamer services')
    synthetic_profile_root = $ProfileRoot
    real_profile_files_inspected = $false
    outside_unchanged_scope = 'Controlled synthetic profile paths and named actual Windows integration state; not an exhaustive real-profile audit'
    probe = $Acceptance
}
$ReportPath = Join-Path $AcceptanceDirectory 'qualification.json'
$Before | ConvertTo-Json -Depth 8 | Set-Content (Join-Path $AcceptanceDirectory 'before.json') -Encoding UTF8
$After | ConvertTo-Json -Depth 8 | Set-Content (Join-Path $AcceptanceDirectory 'after.json') -Encoding UTF8
$Report | ConvertTo-Json -Depth 8 | Set-Content $ReportPath -Encoding UTF8
Write-Output "Portable qualification $($Report.status): $ReportPath"
Write-Output "Functional=$Functional medium=$Medium outside_unchanged=$OutsideUnchanged snapshot_complete=$SnapshotsComplete"
if (-not $Complete) { exit 1 }

# Installs the signed Windows executable for this user only. No elevation or service.
$ErrorActionPreference = 'Stop'
$name = 'FrameworkDistiller-v0.5.5.exe'
$source = Join-Path $PSScriptRoot $name
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw "Place $name next to this script before installing."
}
$root = Join-Path $env:LOCALAPPDATA 'Programs\FrameworkDistiller'
$target = Join-Path $root 'FrameworkDistiller.exe'
$protocol = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Software\Classes\frameworkdistiller', $false)
$existing = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Software\Classes\frameworkdistiller\shell\open\command', $false)
if ($null -ne $protocol) {
    $protocol.Close()
    if ($null -eq $existing) {
        throw 'Another handler already owns frameworkdistiller://; installation stopped without changing it.'
    }
}
if ($null -ne $existing) {
    try {
        if ($existing.GetValue('') -ne ('"' + $target + '"')) {
            throw 'Another handler already owns frameworkdistiller://; installation stopped without changing it.'
        }
    } finally { $existing.Close() }
}
$programs = [Environment]::GetFolderPath('Programs')
$shortcutPath = Join-Path $programs 'Framework Distiller.lnk'
$shell = New-Object -ComObject WScript.Shell
if (Test-Path -LiteralPath $shortcutPath) {
    $prior = $shell.CreateShortcut($shortcutPath)
    $sameTarget = [string]::Equals($prior.TargetPath, $target,
                                  [StringComparison]::OrdinalIgnoreCase)
    if (-not $sameTarget -or -not [string]::IsNullOrEmpty($prior.Arguments)) {
        throw 'A different Framework Distiller Start-menu shortcut already exists; installation stopped without changing it.'
    }
}
New-Item -ItemType Directory -Path $root -Force | Out-Null
Copy-Item -LiteralPath $source -Destination $target -Force

# A Start-menu shortcut is the reliable, cold-start taskbar path; the Edge PWA
# alone cannot start the loopback server after a reboot.
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $target
$shortcut.WorkingDirectory = $root
$shortcut.IconLocation = "$target,0"
$shortcut.Description = 'Start the local Framework Distiller study app'
$shortcut.Save()

# The cached offline PWA page can ask Windows to open this launcher. Ignore the
# URI value entirely: no untrusted URL text reaches the executable as an arg.
$key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Software\Classes\frameworkdistiller')
try {
    $key.SetValue('', 'URL:Framework Distiller launcher')
    $key.SetValue('URL Protocol', '')
} finally { $key.Close() }
$key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Software\Classes\frameworkdistiller\shell\open\command')
try { $key.SetValue('', '"' + $target + '"') } finally { $key.Close() }
Write-Output "Installed for this Windows user: $shortcutPath"
Write-Output 'Open Framework Distiller from Start, then right-click its taskbar icon and choose Pin to taskbar.'

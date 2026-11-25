# Removes only this app's per-user launcher and protocol. Never removes study data.
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:LOCALAPPDATA 'Programs\FrameworkDistiller'
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Programs')) 'Framework Distiller.lnk'
$target = Join-Path $root 'FrameworkDistiller.exe'
$protocol = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Software\Classes\frameworkdistiller', $false)
$command = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Software\Classes\frameworkdistiller\shell\open\command', $false)
if ($null -ne $protocol -and $null -eq $command) {
    $protocol.Close()
    throw 'The frameworkdistiller:// handler does not have the expected command; no files or registry keys were removed.'
}
if ($null -ne $command) {
    try {
        if ($command.GetValue('') -ne ('"' + $target + '"')) {
            throw 'The frameworkdistiller:// handler points elsewhere; no files or registry keys were removed.'
        }
    } finally { $command.Close() }
}
$shell = New-Object -ComObject WScript.Shell
if (Test-Path -LiteralPath $shortcutPath) {
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $sameTarget = [string]::Equals($shortcut.TargetPath, $target,
                                  [StringComparison]::OrdinalIgnoreCase)
    if (-not $sameTarget -or -not [string]::IsNullOrEmpty($shortcut.Arguments)) {
        if ($null -ne $protocol) { $protocol.Close() }
        throw 'A different Framework Distiller Start-menu shortcut is present; uninstall stopped without changing it.'
    }
}
foreach ($process in @(Get-Process -Name 'FrameworkDistiller' -ErrorAction SilentlyContinue)) {
    try { $processPath = $process.Path } catch { $processPath = '' }
    if ([string]::IsNullOrWhiteSpace($processPath)) {
        if ($null -ne $protocol) { $protocol.Close() }
        throw 'Could not verify a Framework Distiller process. Quit the app first, then retry uninstall.'
    }
    if ([string]::Equals($processPath, $target, [StringComparison]::OrdinalIgnoreCase)) {
        if ($null -ne $protocol) { $protocol.Close() }
        throw 'Quit Framework Distiller first, then retry uninstall. No installed component was removed.'
    }
}
if (Test-Path -LiteralPath $target) {
    try {
        $probe = [IO.File]::Open($target, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite,
                                 [IO.FileShare]::None)
        $probe.Close()
    } catch {
        if ($null -ne $protocol) { $protocol.Close() }
        throw 'The installed executable is in use. Quit Framework Distiller, then retry uninstall.'
    }
}
# Remove the EXE first: a late file lock must not leave the shortcut and URI
# deleted while the executable remains installed.
if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target }
if (Test-Path -LiteralPath $shortcutPath) { Remove-Item -LiteralPath $shortcutPath }
if ($null -ne $protocol) {
    $protocol.Close()
    [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree('Software\Classes\frameworkdistiller')
}
if ((Test-Path -LiteralPath $root) -and -not (Get-ChildItem -LiteralPath $root -Force)) {
    Remove-Item -LiteralPath $root
}
Write-Output 'Launcher removed. Your study library under %LOCALAPPDATA%\FrameworkDistiller was not touched.'

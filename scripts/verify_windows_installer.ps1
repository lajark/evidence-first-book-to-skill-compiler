[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Artifact,
    [string]$InstallRoot,
    [switch]$KeepInstall,
    [switch]$AllowExplicitRoot,
    [ValidateRange(0, 60)]
    [int]$LaunchSmokeSeconds = 0
)

$ErrorActionPreference = "Stop"
$artifactPath = (Resolve-Path -LiteralPath $Artifact).Path
if ([IO.Path]::GetExtension($artifactPath).ToLowerInvariant() -ne ".exe") {
    throw "-Artifact must point to a Windows installer executable."
}

$tempRoot = (Resolve-Path -LiteralPath ([IO.Path]::GetTempPath())).Path.TrimEnd("\")
if ($InstallRoot) {
    if (Test-Path -LiteralPath $InstallRoot) {
        throw "-InstallRoot must not already exist; use a fresh temporary directory."
    }
    $candidateRoot = [IO.Path]::GetFullPath($InstallRoot)
} else {
    $candidateRoot = Join-Path $tempRoot ("Book2Skill-installer-smoke-" + [guid]::NewGuid().ToString("N"))
}
$tempPrefix = $tempRoot + "\"
if ($candidateRoot.Equals($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or
    (-not $candidateRoot.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase) -and
        -not $AllowExplicitRoot)) {
    throw "-InstallRoot must be a new child directory under the current user's TEMP directory."
}
$createdRoot = $false
$appProcess = $null
$installRootPath = (New-Item -ItemType Directory -Force -Path $candidateRoot).FullName
$createdRoot = $true
$installRootPath = (Resolve-Path -LiteralPath $installRootPath).Path.TrimEnd("\")
$logPath = Join-Path $installRootPath "installer.log"

$installerArguments = @(
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/SP-",
    "/NOICONS",
    "/DIR=$installRootPath",
    "/LOG=$logPath"
)

function Invoke-Installer([string]$Phase) {
    $process = Start-Process -FilePath $artifactPath -ArgumentList $installerArguments -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        $details = @()
        if (Test-Path -LiteralPath $logPath) {
            $details = Get-Content -LiteralPath $logPath |
                Select-String -Pattern "Fatal exception|Setup was not completed|Error [0-9]+" |
                Select-Object -Last 4 |
                ForEach-Object { $_.Line.Trim() }
        }
        $suffix = if ($details) { " Details: " + ($details -join " | ") } else { "" }
        throw "$Phase installer run failed with exit code $($process.ExitCode).$suffix"
    }
    return $process.ExitCode
}

function Assert-Installed {
    $executable = Join-Path $installRootPath "Book2Skill.exe"
    $uninstaller = Join-Path $installRootPath "unins000.exe"
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "Installed executable was not found: $executable"
    }
    if (-not (Test-Path -LiteralPath $uninstaller -PathType Leaf)) {
        throw "Uninstaller was not found: $uninstaller"
    }
    return $uninstaller
}

try {
    $installExitCode = Invoke-Installer "Initial"
    $uninstaller = Assert-Installed

    $upgradeExitCode = Invoke-Installer "Upgrade"
    $uninstaller = Assert-Installed

    $launchSmokeStatus = "not_requested"
    if ($LaunchSmokeSeconds -gt 0) {
        $executable = Join-Path $installRootPath "Book2Skill.exe"
        $appProcess = Start-Process -FilePath $executable -PassThru
        Start-Sleep -Seconds $LaunchSmokeSeconds
        if ($appProcess.HasExited) {
            throw "Desktop executable exited during launch smoke test with code $($appProcess.ExitCode)."
        }
        $launchSmokeStatus = "passed"
        $appProcess.CloseMainWindow() | Out-Null
        if (-not $appProcess.WaitForExit(3000)) {
            Stop-Process -Id $appProcess.Id -Force
            $appProcess.WaitForExit()
        }
        $appProcess = $null
    }

    $uninstallProcess = Start-Process -FilePath $uninstaller -ArgumentList @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"
    ) -Wait -PassThru
    if ($uninstallProcess.ExitCode -ne 0) {
        throw "Uninstall failed with exit code $($uninstallProcess.ExitCode)."
    }

    [ordered]@{
        schema_version = 1
        artifact = Split-Path -Leaf $artifactPath
        install_root = $installRootPath
        install_exit_code = $installExitCode
        upgrade_exit_code = $upgradeExitCode
        launch_smoke_status = $launchSmokeStatus
        uninstall_exit_code = $uninstallProcess.ExitCode
        status = "passed"
    } | ConvertTo-Json -Compress
}
finally {
    if ($appProcess -and -not $appProcess.HasExited) {
        Stop-Process -Id $appProcess.Id -Force -ErrorAction SilentlyContinue
    }
    if ($createdRoot -and -not $KeepInstall -and (Test-Path -LiteralPath $installRootPath)) {
        $resolvedRoot = (Resolve-Path -LiteralPath $installRootPath).Path.TrimEnd("\")
        if ($AllowExplicitRoot -or
            $resolvedRoot.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $resolvedRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

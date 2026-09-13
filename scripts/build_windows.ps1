param(
    [switch]$SkipInstaller,
    [switch]$ReleaseReady,
    [switch]$Sign,
    [string]$CertificateThumbprint,
    [string]$CertificatePath,
    [string]$TimestampUrl,
    [switch]$AllowUntrustedSignature
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$SpecPath = Join-Path $ProjectDir "packaging\windows\Book2Skill.spec"
$IssPath = Join-Path $ProjectDir "packaging\windows\Book2Skill.iss"
$DependencyManifestScript = Join-Path $ProjectDir "scripts\generate_desktop_dependency_manifest.py"
$SignScript = Join-Path $ProjectDir "scripts\sign_windows_release.ps1"
$DependencyManifestPath = Join-Path $ProjectDir "dist\installer\dependency-manifest.json"
$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PythonExe)) {
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $PythonCommand) {
        throw "Python was not found. Create .venv or add python to PATH."
    }
    $PythonExe = $PythonCommand.Source
}

Push-Location $ProjectDir
try {
    $AppVersion = (& $PythonExe -c "import book2skill; print(book2skill.__version__)").Trim()
    if ($LASTEXITCODE -ne 0 -or -not $AppVersion) {
        throw "Unable to read Book2Skill version."
    }

    if ($ReleaseReady) {
        $license = Get-Content (Join-Path $ProjectDir "LICENSE") -Raw
        if ($license -match "Private Use Notice") {
            throw "External release is blocked by the current private-use LICENSE."
        }
        if (-not $Sign) {
            throw "External Windows installer release requires -Sign with a trusted certificate."
        }
    }

    & $PythonExe $DependencyManifestScript --output $DependencyManifestPath
    if ($LASTEXITCODE -ne 0) {
        throw "Desktop dependency manifest generation failed with exit code $LASTEXITCODE."
    }

    & $PythonExe -m PyInstaller --clean --noconfirm $SpecPath
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }

    if ($SkipInstaller) {
        Write-Host "Desktop build completed: dist\Book2Skill\Book2Skill.exe"
        exit 0
    }

    $CompilerCandidates = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    $InnoCompiler = $CompilerCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $InnoCompiler) {
        throw "Inno Setup 6 not found. Install it or rerun with -SkipInstaller."
    }

    & $InnoCompiler "/DMyAppVersion=$AppVersion" $IssPath
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup compiler failed with exit code $LASTEXITCODE."
    }

    $Artifact = Join-Path $ProjectDir "dist\installer\Book2Skill-Setup-$AppVersion-windows-x64.exe"
    if (-not (Test-Path -LiteralPath $Artifact)) {
        throw "Installer compiler finished without creating $Artifact."
    }

    $Signature = $null
    if ($Sign) {
        if ([bool]$CertificateThumbprint -eq [bool]$CertificatePath) {
            throw "Exactly one of -CertificateThumbprint or -CertificatePath is required when -Sign is used."
        }
        if ($CertificateThumbprint) {
            if ($AllowUntrustedSignature) {
                $SignatureOutput = & $SignScript -Artifact $Artifact -CertificateThumbprint $CertificateThumbprint -TimestampUrl $TimestampUrl -AllowUntrusted
            } elseif ($TimestampUrl) {
                $SignatureOutput = & $SignScript -Artifact $Artifact -CertificateThumbprint $CertificateThumbprint -TimestampUrl $TimestampUrl
            } else {
                $SignatureOutput = & $SignScript -Artifact $Artifact -CertificateThumbprint $CertificateThumbprint
            }
        } else {
            $PowerShell7 = Get-Command pwsh.exe -ErrorAction SilentlyContinue
            if (-not $PowerShell7) {
                throw "PowerShell 7 (pwsh.exe) is required for -CertificatePath signing."
            }
            $SignerArgs = @(
                "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $SignScript,
                "-Artifact", $Artifact, "-CertificatePath", $CertificatePath
            )
            if ($TimestampUrl) { $SignerArgs += @("-TimestampUrl", $TimestampUrl) }
            if ($AllowUntrustedSignature) { $SignerArgs += "-AllowUntrusted" }
            $SignatureOutput = & $PowerShell7.Source @SignerArgs
        }
        if ($LASTEXITCODE -ne 0) {
            throw "Windows Authenticode signing failed with exit code $LASTEXITCODE."
        }
        $Signature = ($SignatureOutput -join "`n") | ConvertFrom-Json
    }

    $MetadataArgs = @(
        "scripts\generate_desktop_release.py",
        "--repo", ".",
        "--artifact", $Artifact,
        "--version", $AppVersion,
        "--output-dir", "dist\installer"
    )
    if ($ReleaseReady) { $MetadataArgs += "--release-ready" }
    if ($Signature) {
        $MetadataArgs += @(
            "--signature-status", $Signature.signature_status,
            "--signature-subject", $Signature.signer_subject,
            "--signature-thumbprint", $Signature.signer_thumbprint,
            "--signature-timestamp-status", $Signature.timestamp_status
        )
    } else {
        $MetadataArgs += @(
            "--signature-status", "unsigned",
            "--signature-timestamp-status", "not_applicable"
        )
    }
    & $PythonExe @MetadataArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Desktop release metadata generation failed with exit code $LASTEXITCODE."
    }
    Write-Host "Installer build completed: $Artifact"
}
finally {
    Pop-Location
}

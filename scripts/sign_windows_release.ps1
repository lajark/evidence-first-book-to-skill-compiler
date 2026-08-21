param(
    [Parameter(Mandatory = $true)]
    [string]$Artifact,
    [string]$CertificateThumbprint,
    [string]$CertificatePath,
    [string]$TimestampUrl,
    [switch]$AllowUntrusted
)

$ErrorActionPreference = "Stop"
$artifactPath = (Resolve-Path -LiteralPath $Artifact).Path
$normalizedThumbprint = ($CertificateThumbprint -replace "\s", "").ToUpperInvariant()

if ([bool]$CertificateThumbprint -eq [bool]$CertificatePath) {
    throw "Pass exactly one of -CertificateThumbprint or -CertificatePath."
}

$certificatePassword = $null
if ($CertificatePath) {
    $certificatePath = (Resolve-Path -LiteralPath $CertificatePath).Path
    $certificatePassword = $env:BOOK2SKILL_SIGNING_PASSWORD
    if (-not $certificatePassword) {
        throw "BOOK2SKILL_SIGNING_PASSWORD is required for -CertificatePath."
    }
    $storageFlags = [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::EphemeralKeySet
    if ($PSVersionTable.PSEdition -eq "Core") {
        $certificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new(
            $certificatePath, $certificatePassword, $storageFlags
        )
    } else {
        $certificate = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2
        $certificate.Import($certificatePath, $certificatePassword, $storageFlags)
    }
    $normalizedThumbprint = $certificate.Thumbprint
} else {
    $certificate = Get-ChildItem Cert:\CurrentUser\My |
        Where-Object { $_.Thumbprint -eq $normalizedThumbprint } |
        Select-Object -First 1
    if (-not $certificate) {
        throw "Signing certificate was not found in CurrentUser\My: $normalizedThumbprint"
    }
    if (-not $certificate.HasPrivateKey) {
        throw "Signing certificate has no private key: $normalizedThumbprint"
    }
}

$signToolCandidates = @(
    "${env:ProgramFiles(x86)}\Windows Kits\10\bin\10.0.26100.0\x64\signtool.exe",
    "${env:ProgramFiles(x86)}\Windows Kits\10\bin\10.0.22621.0\x64\signtool.exe",
    "${env:ProgramFiles(x86)}\Windows Kits\10\bin\x64\signtool.exe"
)
$signTool = $signToolCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $signTool) {
    $command = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($command) { $signTool = $command.Source }
}
if (-not $signTool) {
    throw "Windows SDK signtool.exe was not found."
}

$signArgs = @(
    "sign",
    "/fd", "SHA256",
    "/d", "Book2Skill",
    "/du", "https://github.com/lajark/evidence-first-book-to-skill-compiler"
)
if ($TimestampUrl) {
    $timestampUri = $null
    if (-not [Uri]::TryCreate($TimestampUrl, [UriKind]::Absolute, [ref]$timestampUri) -or
        $timestampUri.Scheme -notin @("http", "https")) {
        throw "-TimestampUrl must be an absolute HTTP(S) URL."
    }
    $signArgs += @("/tr", $TimestampUrl, "/td", "SHA256")
}
if ($CertificatePath) {
    $signArgs += @("/f", $certificatePath, "/p", $certificatePassword)
} else {
    $signArgs += @("/sha1", $normalizedThumbprint)
}
$signArgs += $artifactPath
& $signTool @signArgs *> $null
if ($LASTEXITCODE -ne 0) {
    throw "signtool sign failed with exit code $LASTEXITCODE."
}

$signature = Get-AuthenticodeSignature -FilePath $artifactPath
$signer = $signature.SignerCertificate
if (-not $signer -or $signer.Thumbprint -ne $normalizedThumbprint) {
    throw "The signed artifact does not contain the requested signer certificate."
}

$trusted = $signature.Status -eq "Valid"
if (-not $trusted -and -not $AllowUntrusted) {
    throw "Signature chain is not trusted ($($signature.Status)); pass -AllowUntrusted only for an internal preview."
}
$status = if ($trusted) { "trusted" } elseif ($certificate.Subject -eq $certificate.Issuer) {
    "self_signed_untrusted"
} else {
    "signed_untrusted"
}
$timestampStatus = if (-not $TimestampUrl) {
    "not_requested"
} elseif ($signature.TimeStamperCertificate) {
    "present"
} else {
    "missing"
}
if ($TimestampUrl -and $timestampStatus -ne "present") {
    throw "The signed artifact does not contain an RFC 3161 timestamp."
}

[ordered]@{
    schema_version = 1
    artifact = Split-Path -Leaf $artifactPath
    signature_status = $status
    signer_subject = $signer.Subject
    signer_thumbprint = $signer.Thumbprint
    authenticode_status = [string]$signature.Status
    timestamp_status = $timestampStatus
} | ConvertTo-Json -Compress

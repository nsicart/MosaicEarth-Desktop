param(
    [Parameter(Mandatory=$true)][string]$Path,
    [string]$CertificateThumbprint = $env:ME_SIGNING_CERTIFICATE_THUMBPRINT,
    [string]$SignTool = $env:ME_SIGNTOOL,
    [string]$TimestampUrl = 'http://time.certum.pl'
)
$ErrorActionPreference = 'Stop'
if (-not $SignTool) { $SignTool = (Get-Command signtool.exe -ErrorAction Stop).Source }
if ($CertificateThumbprint -notmatch '^[A-Fa-f0-9]{40}$') {
    throw 'Set the complete thumbprint of the identity-validated code-signing certificate.'
}
$file = (Get-Item -LiteralPath $Path).FullName
$cert = Get-Item -LiteralPath "Cert:\CurrentUser\My\$CertificateThumbprint"
if (-not $cert.HasPrivateKey -or $cert.NotAfter -le (Get-Date)) {
    throw 'The signing certificate is expired or its HSM/private key is unavailable.'
}
$usages = @($cert.Extensions | Where-Object {
    $_ -is [System.Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension]
} | ForEach-Object { $_.EnhancedKeyUsages } | ForEach-Object { $_.Value })
if (-not ($usages -contains '1.3.6.1.5.5.7.3.3')) {
    throw 'This certificate is not valid for code signing.'
}
# SimplySign or a hardware-token provider must already be unlocked by its owner.
& $SignTool sign /sha1 $CertificateThumbprint /s My /fd SHA256 /tr $TimestampUrl /td SHA256 /d 'MosaicEarth Desktop' $file
if ($LASTEXITCODE -ne 0) { throw 'Code signing or timestamping failed.' }
& $SignTool verify /pa /all /v $file
if ($LASTEXITCODE -ne 0) { throw 'Windows rejected the Authenticode signature.' }
$signature = Get-AuthenticodeSignature -LiteralPath $file
if ($signature.Status -ne 'Valid' -or -not $signature.TimeStamperCertificate -or
    $signature.SignerCertificate.Thumbprint -ne $CertificateThumbprint) {
    throw 'Valid publisher signature and timestamp are both required.'
}
Write-Output "Verified Authenticode publisher: $($signature.SignerCertificate.Subject)"

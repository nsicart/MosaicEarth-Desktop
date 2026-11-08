param([Parameter(Mandatory=$true)][string[]]$Path)
$ErrorActionPreference = 'Stop'
foreach ($file in $Path) {
    $signature = Get-AuthenticodeSignature -LiteralPath $file
    if ($signature.Status -ne 'Valid' -or -not $signature.TimeStamperCertificate) {
        throw "Missing or invalid publisher signature/timestamp: $file"
    }
    [PSCustomObject]@{
        File = (Get-Item -LiteralPath $file).Name
        Status = $signature.Status.ToString()
        Publisher = $signature.SignerCertificate.Subject
        Thumbprint = $signature.SignerCertificate.Thumbprint
        TimestampIssuer = $signature.TimeStamperCertificate.Subject
        SHA256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $file).Hash
    }
}

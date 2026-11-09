# Release signatures

The six 0.4.2 artifacts and SHA256SUMS-0.4.2 have detached OpenPGP signatures. The exact tested installers remain unchanged. Download the public key, matching `.asc` files and `VERIFY-SIGNATURES.txt` from [the release](https://github.com/nsicart/MosaicEarth-Desktop/releases/tag/v0.4.2). The same [verification instructions](https://app.mosaicearth.app/api/connectors/download?target=release-signature-VERIFY-SIGNATURES.txt) and [public key](https://app.mosaicearth.app/api/connectors/download?target=release-signature-MosaicEarth-release-signing.asc) are available from the canonical service.

Release signing key: **0AFC FF8D 326E 8ABB 84DB 7935 B709 8951 E764 CFC2**. Created 2026-11-08, expires 2028-11-07. This is the MosaicEarth project's release key, not a certificate issued by an external identity authority.

```sh
gpg --show-keys --with-fingerprint MosaicEarth-release-signing.asc
gpg --dearmor --output MosaicEarth-release-signing.gpg MosaicEarth-release-signing.asc
gpgv --keyring ./MosaicEarth-release-signing.gpg SHA256SUMS-0.4.2.asc SHA256SUMS-0.4.2
gpgv --keyring ./MosaicEarth-release-signing.gpg mosaicearth-desktop_0.4.2_amd64.deb.asc mosaicearth-desktop_0.4.2_amd64.deb
```

Compare the fingerprint with the project's independently obtained fingerprint before trusting the key. Direct `apt install ./package.deb` does not automatically verify a detached signature; verify first. Release verification uses a clean keyring and rejects a modified Debian package.

## Windows Authenticode status

The 0.4.2 EXE still lacks a Windows-trusted publisher signature. OpenPGP does not replace Authenticode. Completion requires an identity-validated code-signing certificate, an RFC3161 timestamp, signature verification under Windows, and validation of the signed installer and embedded uninstaller. SmartScreen reputation is separate from signature validity.

The build accepts an optional absolute `ME_SIGNING_COMMAND` executable. NSIS calls it for the uninstaller before compression, then for the installer; any nonzero exit aborts the build. The wrapper must sign **and verify** its one file argument. `scripts/sign-windows.ps1` supplies that operation using Windows SDK SignTool and the owner's unlocked SimplySign/hardware-backed certificate in CurrentUser\My. `scripts/verify-windows-signature.ps1` records publisher, timestamp and SHA-256. These Windows certificate operations remain unexecuted until the real certificate is available; the NSIS success/failure hooks are tested separately.

For an individual publisher in Spain distributing a paid installer, Certum Standard Code Signing in the Cloud is compatible. Its Open Source discounted certificate prohibits commercial distribution. Azure Artifact Signing public individual certificates currently support US/Canada only; Azure Sponsorship subscriptions also cannot host this service. The publisher has postponed the certificate purchase. No certificate has been purchased or external identity data submitted. Publishing as a company does not automatically make signing free.

## Release operator

Keep all private keys, passphrases and certificate credentials outside this repository. `scripts/sign-release.py` validates every artifact hash against the supplied manifest before signing. Supply the private GnuPG home, complete fingerprint, passphrase file, artifact directory, checksum manifest and signature output directory explicitly. Publish only the public key, signatures and verification instructions. Keep a protected backup and the key's revocation certificate.

Sign Authenticode **before** computing the public SHA-256 manifest and its OpenPGP signatures. Never reuse an old manifest after changing a binary. Publish a new tested package revision for the Authenticode-enabled build.

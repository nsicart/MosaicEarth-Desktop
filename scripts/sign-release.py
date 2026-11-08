#!/usr/bin/env python3
"""Sign verified release files using an operator-managed OpenPGP key.

This does not add a Windows Authenticode signature or imply OS trust.
Private key material must live outside the checkout.
"""
import argparse
import hashlib
import re
import subprocess
from pathlib import Path


def release_files(directory, manifest):
    files = []
    seen = set()
    for line in manifest.read_text().splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  ([A-Za-z0-9_.-]+)', line)
        if not match or match[2] in {'.', '..'} or match[2] in seen:
            raise ValueError('Invalid or duplicate manifest entry')
        digest, name = match.groups()
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Not a regular release file: {name}')
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != digest:
            raise ValueError(f'Release checksum mismatch: {name}')
        seen.add(name)
        files.append(path)
    if not files:
        raise ValueError('Empty release manifest')
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--gnupghome', type=Path, required=True)
    parser.add_argument('--fingerprint', required=True)
    parser.add_argument('--passphrase-file', type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9A-Fa-f]{40,64}', args.fingerprint):
        parser.error('Use the complete signing-key fingerprint')
    files = release_files(args.directory.resolve(), args.manifest.resolve())
    args.output.mkdir(parents=True, exist_ok=True)
    gpg = ['gpg', '--homedir', str(args.gnupghome), '--batch', '--yes']
    public = args.output / 'MosaicEarth-release-signing.asc'
    with public.open('wb') as stream:
        subprocess.run([*gpg, '--armor', '--export', args.fingerprint], stdout=stream, check=True)
    if not public.stat().st_size:
        raise ValueError('Signing key not found')
    for path in [*files, args.manifest.resolve()]:
        subprocess.run([*gpg, '--pinentry-mode', 'loopback', '--passphrase-file',
                        str(args.passphrase_file), '--local-user', args.fingerprint,
                        '--armor', '--detach-sign', '--output',
                        str(args.output / (path.name + '.asc')), str(path)], check=True)
    print(f'Signed {len(files)} artifacts and the checksum manifest')


if __name__ == '__main__':
    main()

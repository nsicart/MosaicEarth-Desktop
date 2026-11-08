"""Verify account grants for the official commercial distribution.

The open-source runtime has no account requirement. Grants authorize the
commercial installation/provisioning service; they do not change the AGPL.
"""
import argparse
import base64
import hashlib
import json
import os
import platform
import time
from pathlib import Path
from mosaicearth_sync import Client, SyncError


def device_identity():
    if os.name == "nt":
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            value = str(winreg.QueryValueEx(key, "MachineGuid")[0])
    else:
        value = Path("/etc/machine-id").read_text().strip()
    return hashlib.sha256(("mosaicearth-device-v1:" + value).encode()).hexdigest()


def verify_grant(grant, public_key, device=None, now=None):
    try:
        payload = base64.b64decode(grant["payload"], validate=True)
        signature = base64.b64decode(grant["signature"], validate=True)
        n, e = int(public_key["n"], 16), int(public_key["e"])
        size = (n.bit_length() + 7) // 8
        if len(payload) > 8192 or len(signature) != size or size < 256 or int.from_bytes(signature, "big") >= n:
            raise ValueError
        # Strict EMSA-PKCS1-v1_5 verification with the SHA-256 DigestInfo prefix.
        digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(payload).digest()
        expected = b"\x00\x01" + b"\xff" * (size - len(digest_info) - 3) + b"\x00" + digest_info
        decoded = pow(int.from_bytes(signature, "big"), e, n).to_bytes(size, "big")
        if decoded != expected:
            raise ValueError
        data = json.loads(payload)
        current = int(time.time() if now is None else now)
        if (data["schema"] != 1 or data["edition"] != "commercial" or data["device"] != (device or device_identity())
                or not data["issued_at"] - 300 <= current < data["expires_at"]
                or data["expires_at"] - data["issued_at"] > 31 * 86400):
            raise ValueError
        return data
    except (ValueError, KeyError, TypeError, OSError):
        raise SyncError("Activate this device with the account that purchased MosaicEarth Desktop") from None


def activate(email, password, root, public_key):
    client = Client()
    try:
        client.signin(email, password)
        data = client.request("POST", "/api/v2/desktop/activate", {"device": device_identity(), "name": platform.node()})
        account = save_grant(data["grant"], root, public_key)
        return {"ok": True, "email": account['email']}
    finally:
        if client.token:
            client.signout()


def save_grant(grant, root, public_key):
    account = verify_grant(grant, public_key, device=os.environ.get('MOSAICEARTH_HOST_DEVICE') or None)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / 'account-grant.tmp'
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as stream:
        json.dump(grant, stream)
    temporary.replace(root / 'account-grant.json')
    return account


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--activate-file", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--public-key", type=Path, default=Path(__file__).resolve().parents[1] / "resources/license-public.json")
    args = parser.parse_args()
    try:
        raw = args.activate_file.read_bytes()
        args.activate_file.unlink()
        fields = raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-16-le").splitlines()
        if len(fields) != 2:
            raise SyncError("Enter your email and password")
        activate(fields[0], fields[1], args.data, json.loads(args.public_key.read_text()))
        print("Account activated")
    except Exception as exc:
        args.activate_file.unlink(missing_ok=True)
        print(str(exc) if isinstance(exc, SyncError) else "Activation could not be completed. Check your connection and try again.")
        raise SystemExit(1)

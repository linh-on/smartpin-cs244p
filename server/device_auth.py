"""Checks that a device report really comes from that device and was not changed.

Each device signs the exact bytes of its JSON body and sends the signature,
hex-encoded, in the X-Signature header. Two methods are supported:

  hmac   HMAC-SHA256 with a secret shared by the device and the server.
  ecdsa  ECDSA P-256 / SHA-256 made by an ATECC608A secure element. The server
         only holds the public key, so it can check signatures but not make them.

DEVICE_KEYS lists one entry per device, separated by commas:

  DEVICE_KEYS=bin-1=hmac:<secret>,bin-2=ecdsa:<public key, 128 hex chars>

For ecdsa the public key is the 64 raw bytes X||Y that the ATECC608A gives out,
and the signature is the 64 raw bytes r||s.
"""
import hashlib
import hmac
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

METHODS = ("hmac", "ecdsa")


@dataclass(frozen=True)
class DeviceKey:
    method: str
    hmac_secret: bytes | None = None
    public_key: ec.EllipticCurvePublicKey | None = None


def parse_device_keys(text: str) -> dict[str, DeviceKey]:
    keys = {}
    for entry in filter(None, (part.strip() for part in text.split(","))):
        device_id, _, rest = entry.partition("=")
        method, _, value = rest.partition(":")
        device_id, method, value = device_id.strip(), method.strip(), value.strip()
        if not device_id or method not in METHODS or not value:
            raise ValueError(
                f"Bad DEVICE_KEYS entry {device_id or entry!r}: "
                "expected <device_id>=hmac:<secret> or <device_id>=ecdsa:<public key hex>"
            )
        if method == "hmac":
            keys[device_id] = DeviceKey("hmac", hmac_secret=value.encode())
        else:
            keys[device_id] = DeviceKey("ecdsa", public_key=_public_key_from_hex(device_id, value))
    return keys


def _public_key_from_hex(device_id: str, value: str) -> ec.EllipticCurvePublicKey:
    try:
        raw = bytes.fromhex(value)
    except ValueError:
        raw = b""
    if len(raw) != 64:
        raise ValueError(f"DEVICE_KEYS: public key for {device_id!r} must be 128 hex characters (X||Y)")
    x = int.from_bytes(raw[:32], "big")
    y = int.from_bytes(raw[32:], "big")
    return ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()


def signature_is_valid(key: DeviceKey, body: bytes, signature_hex: str) -> bool:
    try:
        signature = bytes.fromhex(signature_hex.strip())
    except ValueError:
        return False

    if key.method == "hmac":
        expected = hmac.new(key.hmac_secret, body, hashlib.sha256).digest()
        return hmac.compare_digest(expected, signature)

    if len(signature) != 64:
        return False
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    try:
        key.public_key.verify(encode_dss_signature(r, s), body, ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False

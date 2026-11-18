"""Password hashing for dashboard accounts.

We never store the password itself, only a scrypt hash with a random salt.
scrypt is slow on purpose, so guessing passwords from a stolen database is expensive.
It is part of Python's standard library, so no extra package is needed.
"""
import hashlib
import hmac
import secrets

SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
HASH_LENGTH = 32
MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    """Returns a string like scrypt$16384$8$1$<salt hex>$<hash hex>."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R,
                            p=SCRYPT_P, dklen=HASH_LENGTH)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def password_matches(password: str, stored_hash: str) -> bool:
    try:
        method, n, r, p, salt_hex, digest_hex = stored_hash.split("$")
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        n, r, p = int(n), int(r), int(p)
    except ValueError:
        return False
    if method != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=len(expected))
    return hmac.compare_digest(digest, expected)


# Checked when someone logs in with a username that does not exist, so a wrong
# username takes as long as a wrong password and does not reveal which usernames exist.
UNKNOWN_USER_HASH = hash_password(secrets.token_hex(16))

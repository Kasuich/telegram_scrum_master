"""Small password/session helpers for the console MVP."""

from __future__ import annotations

import hashlib
import hmac
import secrets

_ITERATIONS = 210_000
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    salt = secrets.token_hex(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            salt.encode(),
            int(iterations),
        )
    except Exception:
        return False
    return hmac.compare_digest(digest.hex(), expected)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_login_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_login_code(challenge_id: str, code: str, secret: str) -> str:
    payload = f"{challenge_id}:{code}".encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

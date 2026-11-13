"""Security helpers: password hashing, secret encryption, CSRF tokens."""
import base64
import hashlib
import hmac
import os
import secrets

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, Request

from app.config import get_settings

# ---------------------------------------------------------------- passwords
# PBKDF2-SHA256 with a random salt per user. It ships with Python, so there is
# nothing to compile on Windows. Hash format: pbkdf2_sha256$iterations$salt$hash
_PBKDF2_ITERATIONS = 390_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        _PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = stored_hash.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------- secret storage
# WhatsApp tokens are encrypted before they are saved in the database.

def _fernet() -> Fernet:
    key = hashlib.sha256(get_settings().secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return ""


def mask_secret(value: str) -> str:
    if not value:
        return ""
    return "•" * 8 + value[-4:]


def new_token(length: int = 24) -> str:
    return secrets.token_urlsafe(length)


# --------------------------------------------------------------------- CSRF
# A random token is kept in the signed session cookie and must be sent back
# with every form post (hidden field) or API write (X-CSRF-Token header).

def get_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = new_token()
        request.session["csrf_token"] = token
    return token


def verify_csrf(request: Request, token: str | None) -> None:
    expected = request.session.get("csrf_token")
    if not expected or not token or not hmac.compare_digest(expected, token):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token. Refresh the page and try again.")


async def require_csrf_header(request: Request) -> None:
    """Dependency for JSON API write endpoints."""
    verify_csrf(request, request.headers.get("X-CSRF-Token"))

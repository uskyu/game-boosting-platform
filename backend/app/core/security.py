"""
Security utilities module.
Handles password hashing and JWT token generation/verification.
"""

import base64
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

# Password hashing context using bcrypt
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _get_fernet() -> Fernet:
    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


# =============================================================================
# PASSWORD UTILITIES
# =============================================================================

def hash_password(password: str) -> str:
    """
    Hash a plain text password.

    Args:
        password: Plain text password.

    Returns:
        Hashed password string.
    """
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plain text password against a hash.

    Args:
        plain_password: Plain text password to verify.
        hashed_password: Hashed password to compare against.

    Returns:
        True if password matches, False otherwise.
    """
    return pwd_context.verify(plain_password, hashed_password)


def encrypt_text(plain_text: str | None) -> str | None:
    """
    Encrypt sensitive text for storage.
    Returns None for empty input.
    """
    if plain_text is None:
        return None
    value = plain_text.strip()
    if not value:
        return None
    return _get_fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_text(cipher_text: str | None) -> str | None:
    """
    Decrypt sensitive text.
    Returns None for invalid or empty input.
    """
    if cipher_text is None:
        return None
    value = cipher_text.strip()
    if not value:
        return None
    try:
        return _get_fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


# =============================================================================
# JWT TOKEN UTILITIES
# =============================================================================

def create_access_token(
    subject: str | int,
    expires_delta: timedelta | None = None,
    additional_claims: dict[str, Any] | None = None,
) -> str:
    """
    Create a JWT access token.

    Args:
        subject: Token subject (usually user ID).
        expires_delta: Custom expiration time. Defaults to settings value.
        additional_claims: Additional data to include in token.

    Returns:
        Encoded JWT token string.
    """
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )

    to_encode: dict[str, Any] = {
        "sub": str(subject),
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "access",
    }

    if additional_claims:
        to_encode.update(additional_claims)

    return jwt.encode(
        to_encode,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def create_refresh_token(
    subject: str | int,
    expires_delta: timedelta | None = None,
) -> str:
    """
    Create a JWT refresh token.

    Args:
        subject: Token subject (usually user ID).
        expires_delta: Custom expiration time. Defaults to settings value.

    Returns:
        Encoded JWT refresh token string.
    """
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            days=settings.REFRESH_TOKEN_EXPIRE_DAYS
        )

    to_encode: dict[str, Any] = {
        "sub": str(subject),
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "refresh",
    }

    return jwt.encode(
        to_encode,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def decode_token(token: str) -> dict[str, Any] | None:
    """
    Decode and verify a JWT token.

    Args:
        token: Encoded JWT token string.

    Returns:
        Decoded token payload or None if invalid.
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
        return payload
    except JWTError:
        return None


def verify_token(token: str, token_type: str = "access") -> dict[str, Any] | None:
    """
    Verify a JWT token and check its type.

    Args:
        token: Encoded JWT token string.
        token_type: Expected token type ("access" or "refresh").

    Returns:
        Decoded token payload or None if invalid or wrong type.
    """
    payload = decode_token(token)

    if payload is None:
        return None

    if payload.get("type") != token_type:
        return None

    return payload


# =============================================================================
# SQL UTILITIES
# =============================================================================

def escape_like(value: str) -> str:
    """Escape SQL LIKE wildcard characters so user input is treated literally.

    The backslash is used as the escape character (MySQL / SQLAlchemy default).
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

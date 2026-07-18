"""Pure helpers for rotating values protected by the application secret key."""
from __future__ import annotations

import base64
import hashlib
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

ENCRYPTED_HEADER_PREFIX = "enc:v1:"


class KeyRotationError(ValueError):
    """Raised when a value looks encrypted but cannot be read with the old key."""


def fernet_for_secret(secret: str) -> Fernet:
    if len(secret.strip()) < 32:
        raise ValueError("secret key must contain at least 32 characters")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def _rotate_value(value: str, *, old: Fernet, new: Fernet) -> str:
    try:
        plaintext = old.decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        # Fernet tokens have this stable prefix. Treating an unreadable token as
        # legacy plaintext would silently make a damaged secret unrecoverable.
        if value.startswith("gAAAA"):
            raise KeyRotationError("unreadable encrypted value") from exc
        plaintext = value
    return new.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def rotate_secret_value(value: str | None, *, old_secret: str, new_secret: str) -> str | None:
    if value is None:
        return None
    return _rotate_value(
        value,
        old=fernet_for_secret(old_secret),
        new=fernet_for_secret(new_secret),
    )


def rotate_header_values(headers: dict[str, Any] | None, *, old_secret: str, new_secret: str) -> dict[str, Any]:
    old = fernet_for_secret(old_secret)
    new = fernet_for_secret(new_secret)
    rotated: dict[str, Any] = {}
    for name, value in (headers or {}).items():
        if not isinstance(value, str):
            rotated[name] = value
        elif value.startswith(ENCRYPTED_HEADER_PREFIX):
            rotated[name] = ENCRYPTED_HEADER_PREFIX + _rotate_value(
                value[len(ENCRYPTED_HEADER_PREFIX):], old=old, new=new
            )
        else:
            rotated[name] = ENCRYPTED_HEADER_PREFIX + _rotate_value(value, old=old, new=new)
    return rotated

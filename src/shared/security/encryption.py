"""
Envelope encryption for sensitive fields (LLM API keys, etc).
Uses Fernet symmetric encryption with master key from environment.
"""
import base64
import hashlib
import logging
from cryptography.fernet import Fernet
from src.shared.config import settings

logger = logging.getLogger(__name__)

_ENCRYPTED_HEADER_PREFIX = "enc:v1:"


def _get_fernet() -> Fernet:
    """Derive a Fernet key from the SECRET_KEY setting."""
    secret = settings.SECRET_KEY
    if not secret or secret == "your-secret-key-change-in-production":
        # Development fallback — deterministic but not secure.
        # 安全：仅用于本地开发，生产环境必须配置 SECRET_KEY，否则加密等同于明文。
        # 每次使用回退密钥时记录警告，便于运维察觉配置缺失。
        logger.warning(
            "SECRET_KEY not configured; using deterministic dev fallback key. "
            "This is NOT secure — set a strong SECRET_KEY in production."
        )
        secret = "dev-fallback-key-not-for-production"
    # Derive a 32-byte key using SHA256, then base64-encode for Fernet
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt_value(plain: str | None) -> str | None:
    """Encrypt a plaintext string. Returns None if input is None."""
    if plain is None:
        return None
    f = _get_fernet()
    return f.encrypt(plain.encode()).decode()


def decrypt_value(encrypted: str | None) -> str | None:
    """Decrypt an encrypted string. Returns None if input is None."""
    if encrypted is None:
        return None
    try:
        f = _get_fernet()
        return f.decrypt(encrypted.encode()).decode()
    except Exception:
        # If decryption fails (e.g., value was stored plaintext before encryption was enabled)
        return encrypted


def encrypt_header_values(headers: dict | None) -> dict:
    """Encrypt string header values while keeping the JSON/API shape unchanged."""
    encrypted_headers = {}
    for name, value in (headers or {}).items():
        if isinstance(value, str):
            if value.startswith(_ENCRYPTED_HEADER_PREFIX):
                encrypted_headers[name] = value
            else:
                encrypted_headers[name] = _ENCRYPTED_HEADER_PREFIX + encrypt_value(value)
        else:
            encrypted_headers[name] = value
    return encrypted_headers


def decrypt_header_values(headers: dict | None) -> dict:
    """Decrypt marked values and remain compatible with legacy plaintext rows."""
    decrypted_headers = {}
    for name, value in (headers or {}).items():
        if isinstance(value, str) and value.startswith(_ENCRYPTED_HEADER_PREFIX):
            decrypted_headers[name] = decrypt_value(value[len(_ENCRYPTED_HEADER_PREFIX):])
        else:
            decrypted_headers[name] = value
    return decrypted_headers

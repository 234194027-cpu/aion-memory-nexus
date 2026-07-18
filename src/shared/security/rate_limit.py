"""Shared application rate limiter for authentication endpoints."""

from slowapi import Limiter
from slowapi.util import get_remote_address

from src.shared.config import settings


limiter = Limiter(
    key_func=get_remote_address,
    enabled=not settings.TESTING,
    headers_enabled=True,
    # Application settings are loaded centrally with explicit UTF-8 handling.
    # SlowAPI otherwise re-reads .env using the Windows locale at import time.
    config_filename=__file__,
)


def auth_rate_limit(fn):
    return limiter.limit("10/minute")(fn)

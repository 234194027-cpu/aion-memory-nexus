"""Validation for user-configurable outbound LLM endpoints."""

from __future__ import annotations

import asyncio
import ipaddress
from socket import gaierror, getaddrinfo
from urllib.parse import urlparse


_LOCAL_OLLAMA_HOSTS = {"localhost", "localhost.localdomain"}
_LOCAL_OLLAMA_PORT = 11434


async def assert_safe_llm_endpoint(url: str, api_format: str = "openai") -> None:
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("invalid_llm_endpoint")
    if parsed.username or parsed.password:
        raise ValueError("url_credentials_not_allowed")

    hostname = parsed.hostname.lower()
    try:
        infos = await asyncio.to_thread(getaddrinfo, hostname, parsed.port)
    except (gaierror, OSError) as exc:
        raise ValueError("dns_lookup_failed") from exc

    addresses = {ipaddress.ip_address(info[4][0]) for info in infos}
    local_ollama = (
        (api_format or "openai").lower() == "ollama"
        and (parsed.port or _LOCAL_OLLAMA_PORT) == _LOCAL_OLLAMA_PORT
        and (
            hostname in _LOCAL_OLLAMA_HOSTS
            or (addresses and all(address.is_loopback for address in addresses))
        )
    )
    if local_ollama:
        return

    if parsed.scheme != "https":
        raise ValueError("https_required_for_remote_llm_endpoint")
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("private_or_reserved_address_not_allowed")

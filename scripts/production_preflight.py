"""Read-only production configuration preflight; never prints secret values."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import socket
import ssl
import sys
from urllib.parse import urlparse


PLACEHOLDER_MARKERS = ("replace-with", "your-", "changeme", "example", "<")
REQUIRED_ENV_KEYS = ("POSTGRES_PASSWORD", "SECRET_KEY", "CORS_ORIGINS")


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # pass | fail | manual
    detail: str


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def is_safe_secret(value: str) -> bool:
    normalized = value.strip().lower()
    return bool(normalized) and not any(marker in normalized for marker in PLACEHOLDER_MARKERS)


def is_enabled(value: str) -> bool:
    """Match the deployment env convention without accepting an empty value."""
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _check_env(env: dict[str, str]) -> list[Check]:
    checks: list[Check] = []
    for key in REQUIRED_ENV_KEYS:
        configured = bool(env.get(key, "").strip()) if key == "CORS_ORIGINS" else is_safe_secret(env.get(key, ""))
        checks.append(Check(
            f"env_{key.lower()}",
            "pass" if configured else "fail",
            f"{key} is configured" if configured else f"{key} is missing or still a placeholder",
        ))

    secret = env.get("SECRET_KEY", "")
    checks.append(Check(
        "secret_key_length",
        "pass" if is_safe_secret(secret) and len(secret) >= 32 else "fail",
        "SECRET_KEY has at least 32 characters" if is_safe_secret(secret) and len(secret) >= 32 else "SECRET_KEY must be a non-placeholder value of at least 32 characters",
    ))

    origins = [item.strip() for item in env.get("CORS_ORIGINS", "").split(",") if item.strip()]
    cors_ok = bool(origins) and all(origin.startswith("https://") and "*" not in origin and "localhost" not in origin for origin in origins)
    checks.append(Check(
        "cors_origins",
        "pass" if cors_ok else "fail",
        "CORS origins are explicit HTTPS origins" if cors_ok else "CORS_ORIGINS must contain explicit HTTPS origins without wildcard or localhost",
    ))

    solo_mode = is_enabled(env.get("SOLO_MODE", "false"))
    dev_fallback_enabled = is_enabled(env.get("ALLOW_DEV_AUTH_FALLBACK", "false"))
    explicit_solo = is_enabled(env.get("ALLOW_SOLO_PRODUCTION", "false"))
    system_token_ok = is_safe_secret(env.get("SYSTEM_API_TOKEN", "")) and len(env.get("SYSTEM_API_TOKEN", "")) >= 32
    solo_ok = not dev_fallback_enabled and (
        not solo_mode or (explicit_solo and system_token_ok)
    )
    checks.append(Check(
        "production_auth_mode",
        "pass" if solo_ok else "fail",
        (
            "production authentication is configured"
            if solo_ok
            else "disable development fallback; SOLO_MODE requires ALLOW_SOLO_PRODUCTION=true and a 32-character SYSTEM_API_TOKEN"
        ),
    ))

    redis = env.get("REDIS_URL", "")
    parsed = urlparse(redis)
    redis_url_ok = parsed.scheme in {"redis", "rediss"} and bool(parsed.password)
    redis_password_ok = is_safe_secret(env.get("REDIS_PASSWORD", ""))
    redis_ok = redis_url_ok or redis_password_ok
    checks.append(Check(
        "redis_url_credentials",
        "pass" if redis_ok else "fail",
        (
            "Redis credentials are configured"
            if redis_ok
            else "configure REDIS_URL credentials or REDIS_PASSWORD for a Compose-managed Redis service"
        ),
    ))

    scheduler_enabled = is_enabled(env.get("ENABLE_SCHEDULER", "true"))
    raw_concurrency = env.get("WEB_CONCURRENCY", "1").strip()
    try:
        web_concurrency = int(raw_concurrency)
    except ValueError:
        web_concurrency = 0
    single_leader = web_concurrency > 0 and (not scheduler_enabled or web_concurrency == 1)
    checks.append(Check(
        "scheduler_single_leader",
        "pass" if single_leader else "fail",
        (
            "in-process scheduler has exactly one web worker"
            if scheduler_enabled and single_leader
            else "scheduler is disabled for a multi-worker web deployment"
            if single_leader
            else "ENABLE_SCHEDULER=true requires WEB_CONCURRENCY=1; run scheduling in one designated process"
        ),
    ))
    return checks


def _check_compose(path: Path) -> list[Check]:
    content = path.read_text(encoding="utf-8")
    images = re.findall(r"^\s*image:\s*([^\s#]+)", content, flags=re.MULTILINE)
    # `workspace-init` deliberately reuses the image built by the local `api`
    # service.  It is not pulled from a registry, so an OCI digest would be
    # both unavailable before the build and meaningless as a supply-chain pin.
    # Every externally pulled image must still be immutable.
    externally_pulled_images = [
        image for image in images if image != "life-memory-system-api"
    ]
    pinned = bool(externally_pulled_images) and all(
        "@sha256:" in image for image in externally_pulled_images
    )
    return [
        Check(
            "image_digests",
            "pass" if pinned else "fail",
            "all Compose image references use immutable digests" if pinned else "pin every Compose image to an immutable @sha256 digest",
        ),
        Check(
            "redis_server_auth",
            "pass" if "--requirepass" in content or "aclfile" in content else "fail",
            "Redis server authentication or ACL is configured" if "--requirepass" in content or "aclfile" in content else "configure Redis requirepass or ACL in docker-compose.yml",
        ),
        Check(
            "migration_command",
            "pass" if "alembic upgrade head" in content else "fail",
            "Compose runs Alembic before application/worker startup" if "alembic upgrade head" in content else "add an explicit Alembic upgrade step before serving traffic",
        ),
    ]


def _check_tls(cert_dir: Path, public_url: str | None) -> list[Check]:
    certificate = cert_dir / "life-memory.crt"
    private_key = cert_dir / "life-memory.key"
    files_present = certificate.is_file() and private_key.is_file()
    checks = [Check(
        "tls_certificate_files",
        "pass" if files_present else "fail",
        "Nginx certificate and key files are present" if files_present else "provide certs/life-memory.crt and certs/life-memory.key",
    )]
    if files_present:
        try:
            decoded = ssl._ssl._test_decode_cert(str(certificate))  # type: ignore[attr-defined]
            expires = datetime.strptime(decoded["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
            subject = decoded.get("subject")
            issuer = decoded.get("issuer")
            valid = expires > datetime.now(timezone.utc) and subject != issuer
            checks.append(Check(
                "tls_local_certificate",
                "pass" if valid else "fail",
                "certificate is CA-issued and not expired" if valid else "certificate is expired or self-signed",
            ))
        except (OSError, KeyError, ValueError, ssl.SSLError):
            checks.append(Check("tls_local_certificate", "manual", "certificate contents could not be decoded during local preflight"))

    if public_url:
        parsed = urlparse(public_url)
        host = parsed.hostname
        port = parsed.port or 443
        if parsed.scheme != "https" or not host:
            checks.append(Check("tls_public_chain", "fail", "PUBLIC_BASE_URL must be a valid HTTPS URL"))
        else:
            try:
                context = ssl.create_default_context()
                with socket.create_connection((host, port), timeout=5) as raw:
                    with context.wrap_socket(raw, server_hostname=host) as secured:
                        # A default client context performs both CA-chain and
                        # hostname verification during the TLS handshake.
                        secured.getpeercert()
                checks.append(Check("tls_public_chain", "pass", "public certificate chain and hostname are trusted"))
            except (OSError, ssl.SSLError, ssl.CertificateError):
                checks.append(Check("tls_public_chain", "fail", "public HTTPS certificate chain or hostname validation failed"))
    else:
        checks.append(Check("tls_public_chain", "manual", "run preflight with --public-url to verify the public certificate chain"))
    return checks


def evaluate(*, env_file: Path, compose_file: Path, cert_dir: Path, public_url: str | None = None) -> list[Check]:
    checks: list[Check] = []
    if not env_file.is_file():
        checks.append(Check("env_file", "fail", f"environment file not found: {env_file}"))
    else:
        checks.extend(_check_env(load_env(env_file)))

    if not compose_file.is_file():
        checks.append(Check("compose_file", "fail", f"Compose file not found: {compose_file}"))
    else:
        checks.extend(_check_compose(compose_file))

    checks.extend(_check_tls(cert_dir, public_url))
    checks.append(Check(
        "backup_restore_drill",
        "manual",
        "perform a fresh PostgreSQL backup and restore drill; this cannot be proven from configuration files",
    ))
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Run read-only Life Memory System production preflight checks.")
    parser.add_argument("--env-file", default=".env.production", help="Environment file to inspect without printing values.")
    parser.add_argument("--compose-file", default="docker-compose.yml", help="Compose file to inspect.")
    parser.add_argument("--cert-dir", default="certs", help="Directory containing Nginx TLS files.")
    parser.add_argument("--public-url", help="Public HTTPS URL whose trusted chain and hostname must validate.")
    args = parser.parse_args()

    checks = evaluate(
        env_file=Path(args.env_file),
        compose_file=Path(args.compose_file),
        cert_dir=Path(args.cert_dir),
        public_url=args.public_url,
    )
    for check in checks:
        print(f"[{check.status.upper()}] {check.name}: {check.detail}")
    return 1 if any(check.status == "fail" for check in checks) else 0


if __name__ == "__main__":
    sys.exit(main())

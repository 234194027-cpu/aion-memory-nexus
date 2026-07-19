from pathlib import Path

from scripts.production_preflight import _check_tls, evaluate


def test_preflight_accepts_secure_fixture_without_exposing_values(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.production"
    env_file.write_text(
        "\n".join([
            "POSTGRES_PASSWORD=correct-horse-battery-staple",
            "SECRET_KEY=abcdefghijklmnopqrstuvwxyz0123456789",
            "CORS_ORIGINS=https://memory.example.com",
            "REDIS_URL=rediss://life:redis-secret@redis.example.com:6379/0",
            "SOLO_MODE=false",
            "ALLOW_DEV_AUTH_FALLBACK=false",
        ]),
        encoding="utf-8",
    )
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text(
        "services:\n  redis:\n    image: redis@sha256:abc\n    command: redis-server --requirepass ${REDIS_PASSWORD}\n  api:\n    image: api@sha256:def\n    command: alembic upgrade head\n",
        encoding="utf-8",
    )
    cert_dir = tmp_path / "certs"
    cert_dir.mkdir()
    (cert_dir / "life-memory.crt").write_text("certificate", encoding="utf-8")
    (cert_dir / "life-memory.key").write_text("key", encoding="utf-8")

    checks = evaluate(env_file=env_file, compose_file=compose_file, cert_dir=cert_dir)

    assert not [check for check in checks if check.status == "fail"]
    assert "redis-secret" not in "\n".join(check.detail for check in checks)


def test_preflight_accepts_compose_managed_redis_password(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join([
            "POSTGRES_PASSWORD=correct-horse-battery-staple",
            "SECRET_KEY=abcdefghijklmnopqrstuvwxyz0123456789",
            "CORS_ORIGINS=https://memory.example.com",
            "REDIS_PASSWORD=compose-managed-redis-secret",
            "SOLO_MODE=false",
            "ALLOW_DEV_AUTH_FALLBACK=false",
        ]),
        encoding="utf-8",
    )
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text(
        "services:\n  redis:\n    image: redis@sha256:abc\n    command: redis-server --requirepass ${REDIS_PASSWORD}\n  api:\n    image: api@sha256:def\n    command: alembic upgrade head\n",
        encoding="utf-8",
    )
    cert_dir = tmp_path / "certs"
    cert_dir.mkdir()
    (cert_dir / "life-memory.crt").write_text("certificate", encoding="utf-8")
    (cert_dir / "life-memory.key").write_text("key", encoding="utf-8")

    checks = evaluate(env_file=env_file, compose_file=compose_file, cert_dir=cert_dir)
    by_name = {check.name: check for check in checks}

    assert by_name["redis_url_credentials"].status == "pass"
    assert "compose-managed-redis-secret" not in by_name["redis_url_credentials"].detail


def test_preflight_flags_floating_images_and_unauthenticated_redis(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.production"
    env_file.write_text("POSTGRES_PASSWORD=replace-with-password\n", encoding="utf-8")
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services:\n  redis:\n    image: redis:alpine\n", encoding="utf-8")

    checks = evaluate(env_file=env_file, compose_file=compose_file, cert_dir=tmp_path / "missing-certs")
    by_name = {check.name: check for check in checks}

    assert by_name["image_digests"].status == "fail"
    assert by_name["redis_server_auth"].status == "fail"
    assert by_name["env_postgres_password"].status == "fail"


def test_preflight_blocks_multiple_web_workers_when_in_process_scheduler_is_enabled(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.production"
    env_file.write_text(
        "\n".join([
            "POSTGRES_PASSWORD=correct-horse-battery-staple",
            "SECRET_KEY=abcdefghijklmnopqrstuvwxyz0123456789",
            "CORS_ORIGINS=https://memory.example.com",
            "REDIS_URL=rediss://life:redis-secret@redis.example.com:6379/0",
            "SOLO_MODE=false",
            "ALLOW_DEV_AUTH_FALLBACK=false",
            "ENABLE_SCHEDULER=true",
            "WEB_CONCURRENCY=2",
        ]),
        encoding="utf-8",
    )
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text(
        "services:\n  redis:\n    image: redis@sha256:abc\n    command: redis-server --requirepass ${REDIS_PASSWORD}\n  api:\n    image: api@sha256:def\n    command: alembic upgrade head\n",
        encoding="utf-8",
    )
    cert_dir = tmp_path / "certs"
    cert_dir.mkdir()
    (cert_dir / "life-memory.crt").write_text("certificate", encoding="utf-8")
    (cert_dir / "life-memory.key").write_text("key", encoding="utf-8")

    checks = evaluate(env_file=env_file, compose_file=compose_file, cert_dir=cert_dir)
    by_name = {check.name: check for check in checks}

    assert by_name["scheduler_single_leader"].status == "fail"


def test_preflight_accepts_explicitly_authorized_solo_mode_with_system_token(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join([
            "POSTGRES_PASSWORD=correct-horse-battery-staple",
            "SECRET_KEY=abcdefghijklmnopqrstuvwxyz0123456789",
            "SYSTEM_API_TOKEN=system-token-abcdefghijklmnopqrstuvwxyz012345",
            "CORS_ORIGINS=https://memory.example.com",
            "REDIS_PASSWORD=compose-managed-redis-secret",
            "SOLO_MODE=true",
            "ALLOW_SOLO_PRODUCTION=true",
            "ALLOW_DEV_AUTH_FALLBACK=false",
        ]),
        encoding="utf-8",
    )
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text(
        "services:\n  redis:\n    image: redis@sha256:abc\n    command: redis-server --requirepass ${REDIS_PASSWORD}\n  api:\n    image: api@sha256:def\n    command: alembic upgrade head\n",
        encoding="utf-8",
    )
    cert_dir = tmp_path / "certs"
    cert_dir.mkdir()
    (cert_dir / "life-memory.crt").write_text("certificate", encoding="utf-8")
    (cert_dir / "life-memory.key").write_text("key", encoding="utf-8")

    checks = evaluate(env_file=env_file, compose_file=compose_file, cert_dir=cert_dir)
    by_name = {check.name: check for check in checks}

    assert by_name["production_auth_mode"].status == "pass"


def test_preflight_rejects_non_https_public_url(tmp_path: Path) -> None:
    cert_dir = tmp_path / "certs"
    cert_dir.mkdir()
    (cert_dir / "life-memory.crt").write_text("certificate", encoding="utf-8")
    (cert_dir / "life-memory.key").write_text("key", encoding="utf-8")

    checks = {check.name: check for check in _check_tls(cert_dir, "http://memory.example.com")}

    assert checks["tls_public_chain"].status == "fail"
    assert "HTTPS" in checks["tls_public_chain"].detail

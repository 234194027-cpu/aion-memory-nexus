"""Unit tests for the single source of truth version module (WP-10-T01)."""
from __future__ import annotations

import os
import json
from pathlib import Path

import pytest

from src.shared.version import (
    _VERSION_FILE,
    get_build_commit,
    get_build_time,
    get_product_version,
    get_runtime_profiles,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def test_version_file_exists():
    """The VERSION file must exist at the project root."""
    assert _VERSION_FILE.exists(), f"VERSION file missing at {_VERSION_FILE}"
    assert (PROJECT_ROOT / "VERSION").exists()


def test_version_file_content_matches_reader():
    """get_product_version() must return the contents of VERSION file."""
    raw = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert get_product_version() == raw
    # Sanity check: it must follow semver-ish format
    assert get_product_version().count(".") == 2


def test_frontend_package_version_matches_product_version():
    """Frontend package metadata must not drift from the VERSION source."""
    package = json.loads(
        (PROJECT_ROOT / "admin-web" / "package.json").read_text(encoding="utf-8")
    )
    package_lock = json.loads(
        (PROJECT_ROOT / "admin-web" / "package-lock.json").read_text(encoding="utf-8")
    )
    expected = get_product_version()
    assert package["version"] == expected
    assert package_lock["version"] == expected
    assert package_lock["packages"][""]["version"] == expected
    assert get_product_version().count(".") == 2


def test_get_product_version_is_cached():
    """get_product_version should be cached (lru_cache)."""
    # Calling twice returns the same object due to lru_cache
    v1 = get_product_version()
    v2 = get_product_version()
    assert v1 == v2


def test_get_build_commit_default_unknown(monkeypatch):
    """Without BUILD_COMMIT env var, return 'unknown'."""
    monkeypatch.delenv("BUILD_COMMIT", raising=False)
    assert get_build_commit() == "unknown"


def test_get_build_commit_reads_env(monkeypatch):
    """With BUILD_COMMIT env var, return its value."""
    monkeypatch.setenv("BUILD_COMMIT", "abc1234")
    assert get_build_commit() == "abc1234"


def test_get_build_time_default_unknown(monkeypatch):
    """Without BUILD_TIME env var, return 'unknown'."""
    monkeypatch.delenv("BUILD_TIME", raising=False)
    assert get_build_time() == "unknown"


def test_get_build_time_reads_env(monkeypatch):
    """With BUILD_TIME env var, return its value."""
    monkeypatch.setenv("BUILD_TIME", "2026-07-12T00:00:00Z")
    assert get_build_time() == "2026-07-12T00:00:00Z"


def test_get_runtime_profiles_returns_v2_defaults():
    """V2-only defaults expose the built-in conversational and active working roles."""
    profiles = get_runtime_profiles()
    assert profiles == ["conversational", "working-active"]
    assert isinstance(profiles, list)


def test_get_runtime_profiles_reports_enabled_v2_roles(monkeypatch):
    from src.shared.config import settings

    monkeypatch.setattr(settings, "AGENT_RUNTIME_ENABLED", True)
    monkeypatch.setattr(settings, "CONVERSATIONAL_AGENT_ENABLED", True)
    monkeypatch.setattr(settings, "WORKING_AGENT_SHADOW_ENABLED", True)
    monkeypatch.setattr(settings, "WORKING_AGENT_ACTIVE_ENABLED", False)
    assert get_runtime_profiles() == ["conversational", "working-shadow"]


def test_version_file_is_utf8():
    """VERSION file must be readable as UTF-8 without BOM."""
    content = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert content == get_product_version()
    # Verify no BOM
    raw_bytes = (PROJECT_ROOT / "VERSION").read_bytes()
    assert not raw_bytes.startswith(b"\xef\xbb\xbf")

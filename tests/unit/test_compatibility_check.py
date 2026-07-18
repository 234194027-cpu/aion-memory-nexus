"""Unit tests for startup version compatibility check (WP-10-T06)."""
from __future__ import annotations

from src.shared.version import check_compatibility


def test_compatibility_returns_dict_with_required_keys():
    """check_compatibility must return dict with 'compatible' and 'warnings' keys."""
    result = check_compatibility()
    assert isinstance(result, dict)
    assert "compatible" in result
    assert "warnings" in result
    assert isinstance(result["compatible"], bool)
    assert isinstance(result["warnings"], list)


def test_compatibility_with_matching_frontend_version():
    """When frontend version matches backend version, no version mismatch warning."""
    from src.shared.version import get_product_version

    backend_version = get_product_version()
    result = check_compatibility(expected_frontend_version=backend_version)

    # Should NOT contain frontend version mismatch warning
    frontend_warnings = [
        w for w in result["warnings"] if "does not match" in w
    ]
    assert len(frontend_warnings) == 0, f"Unexpected version mismatch: {frontend_warnings}"


def test_compatibility_with_mismatched_frontend_version():
    """When frontend version differs from backend, a warning is produced."""
    result = check_compatibility(expected_frontend_version="0.0.1")

    mismatch_warnings = [
        w for w in result["warnings"] if "does not match" in w
    ]
    assert len(mismatch_warnings) == 1
    assert "0.0.1" in mismatch_warnings[0]


def test_compatibility_has_no_obsolete_runtime_warning():
    """The completed V2 runtime must not report an obsolete fallback profile."""
    result = check_compatibility()
    assert all("legacy" not in warning.lower() for warning in result["warnings"])


def test_compatibility_validates_semver_format():
    """Version must follow semver format (MAJOR.MINOR.PATCH).

    The current VERSION file must contain a valid semver value.
    """
    result = check_compatibility()
    format_warnings = [
        w for w in result["warnings"] if "semver" in w.lower()
    ]
    assert len(format_warnings) == 0, (
        f"Unexpected semver format warning for valid version: {format_warnings}"
    )


def test_compatibility_does_not_block_startup():
    """check_compatibility should never raise exceptions.

    It returns warnings, not errors. The lifespan startup should not be blocked.
    """
    # Even with mismatched version, function should not raise
    result = check_compatibility(expected_frontend_version="999.999.999")
    assert isinstance(result, dict)
    assert "compatible" in result
    assert "warnings" in result


def test_compatibility_without_frontend_version():
    """When no frontend version is provided, skip frontend check."""
    result = check_compatibility(expected_frontend_version=None)
    # Should not have frontend version mismatch warnings
    frontend_warnings = [
        w for w in result["warnings"] if "does not match" in w
    ]
    assert len(frontend_warnings) == 0


def test_compatibility_warnings_are_human_readable():
    """All warnings should be non-empty human-readable strings."""
    result = check_compatibility()
    for w in result["warnings"]:
        assert isinstance(w, str)
        assert len(w) > 10  # at least a meaningful sentence
        # Should not contain sensitive info
        assert "SECRET" not in w.upper()
        assert "PASSWORD" not in w.upper()
        assert "API_KEY" not in w.upper()

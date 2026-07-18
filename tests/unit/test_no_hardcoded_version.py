"""Regression test: prevent hardcoded version strings (WP-10-T07).

This test scans the repository for hardcoded version strings that should
be derived from the single source of truth (VERSION file + get_product_version()).

Patterns scanned:
  - ``"1.0.0"``  : old hardcoded product version (must not return)
  - ``"0.0.0"``  : default fallback (only allowed in src/shared/version.py)
  - ``version="<X.Y.Z>"`` : hardcoded semver assignment via ``=`` operator
    (JSON ``"version": "..."`` uses colon, not equals, so is naturally excluded)

Whitelisted locations (see ``WHITELIST_PREFIXES``):
  - ``VERSION``                       : the source file itself
  - ``src/shared/version.py``         : the reader (contains ``"0.0.0"`` fallback)
  - ``docs/releases/``                : release notes contain version strings
  - ``package-lock.json``             : third-party dependency versions
  - ``tests/``                        : test fixtures may use version strings
  - ``skills/``                       : separate modules with own version identity

If a new legitimate use appears, add its path to ``WHITELIST_PREFIXES``.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# tests/unit/test_no_hardcoded_version.py -> tests/unit -> tests -> <project_root>
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# --- Patterns ---------------------------------------------------------------

# Old hardcoded product version (must not return to codebase)
PATTERN_OLD_VERSION = re.compile(r'"1\.0\.0"')

# Default fallback version (only allowed in version.py)
PATTERN_DEFAULT_VERSION = re.compile(r'"0\.0\.0"')

# Hardcoded semver assignment: version = "X.Y.Z" or version="X.Y.Z"
# NOTE: This matches the ``=`` assignment operator, not JSON ``"version": "..."``
# which uses a colon. So ``admin-web/package.json``'s ``"version": "2.0.0"``
# is naturally excluded. Media extractors' ``version = "1"`` (single-segment)
# is also excluded because the pattern requires three dot-separated segments.
PATTERN_VERSION_ASSIGN = re.compile(r'version\s*=\s*"[0-9]+\.[0-9]+\.[0-9]+')

PATTERNS: list[tuple[re.Pattern, str]] = [
    (PATTERN_OLD_VERSION, '"1.0.0" (old hardcoded product version)'),
    (PATTERN_DEFAULT_VERSION, '"0.0.0" (default fallback version)'),
    (PATTERN_VERSION_ASSIGN, 'version="X.Y.Z" (hardcoded semver assignment)'),
]

# --- Exclusions -------------------------------------------------------------

# Directories to skip entirely (generated, vendored, cache, etc.)
SKIP_DIRS: set[str] = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".hypothesis",
    ".tox",
    ".eggs",
    "build",
    "dist",
    ".vite",
    ".idea",
    ".vscode",
    "deploy-artifacts",
    "certs",
}

# File extensions to scan (text files that may contain source code or config)
SCAN_EXTENSIONS: set[str] = {
    ".py",
    ".ts",
    ".tsx",
    ".vue",
    ".js",
    ".jsx",
    ".json",
    ".yaml",
    ".yml",
    ".md",
    ".txt",
    ".toml",
    ".cfg",
    ".ini",
}

# Files without extensions to also scan
SCAN_NO_EXT_FILES: set[str] = {
    "Dockerfile",
    "VERSION",
    "Makefile",
}

# --- Whitelist ---------------------------------------------------------------

# Paths are relative to PROJECT_ROOT, normalized to forward slashes.
# A file is whitelisted if its relative path starts with any of these prefixes.
WHITELIST_PREFIXES: list[str] = [
    # The single source of truth
    "VERSION",
    # The reader itself (contains "0.0.0" as fallback)
    "src/shared/version.py",
    # Release notes contain version strings in YAML frontmatter
    "docs/releases/",
    # Third-party dependency versions in npm lockfile
    "admin-web/package-lock.json",
    # npm package metadata is checked against VERSION in test_version_source.py
    "admin-web/package.json",
    # Test fixtures use version strings as inputs
    "tests/",
    # Separate modules with their own version identity
    "skills/",
]


def _is_whitelisted(rel_path: str) -> bool:
    """Check if a relative path (forward slashes) is whitelisted."""
    for prefix in WHITELIST_PREFIXES:
        if rel_path == prefix.rstrip("/") or rel_path.startswith(prefix):
            return True
    return False


def _should_skip_dir(dir_name: str) -> bool:
    """Check if a directory should be skipped during walk."""
    return dir_name in SKIP_DIRS or dir_name.startswith(".venv")


def _should_scan_file(path: Path) -> bool:
    """Check if a file should be scanned based on name/extension."""
    if path.name in SCAN_NO_EXT_FILES:
        return True
    return path.suffix.lower() in SCAN_EXTENSIONS


def _scan_file(path: Path) -> list[tuple[int, str, str]]:
    """Scan a single file for hardcoded version patterns.

    Returns:
        List of (line_number, pattern_description, line_content) tuples.
    """
    violations: list[tuple[int, str, str]] = []
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return violations

    for line_num, line in enumerate(content.splitlines(), start=1):
        for pattern, description in PATTERNS:
            if pattern.search(line):
                violations.append((line_num, description, line.strip()))

    return violations


def test_no_hardcoded_version_strings() -> None:
    """Scan repository for hardcoded version strings.

    Version strings must be derived from the VERSION file via
    ``get_product_version()``. See module docstring for whitelisted locations.

    If a new legitimate use appears, add the path to ``WHITELIST_PREFIXES``.
    """
    all_violations: list[str] = []

    for root, dirs, files in os.walk(PROJECT_ROOT, topdown=True):
        # Prune skipped directories in-place (topdown=True allows mutation)
        dirs[:] = [d for d in dirs if not _should_skip_dir(d)]

        for filename in files:
            filepath = Path(root) / filename
            if not _should_scan_file(filepath):
                continue

            rel_path = filepath.relative_to(PROJECT_ROOT).as_posix()
            if _is_whitelisted(rel_path):
                continue

            violations = _scan_file(filepath)
            for line_num, description, line_content in violations:
                all_violations.append(
                    f"  {rel_path}:{line_num}: {description}\n"
                    f"    -> {line_content}"
                )

    if all_violations:
        failure_msg = (
            f"Found {len(all_violations)} hardcoded version string(s).\n"
            "Version strings should be derived from VERSION file via "
            "get_product_version().\n"
            "If this is a legitimate use, add the file path to "
            "WHITELIST_PREFIXES in this test.\n\n"
            + "\n".join(all_violations)
        )
        raise AssertionError(failure_msg)


def test_whitelist_only_contains_existing_paths() -> None:
    """Whitelist entries should point to paths that exist.

    This prevents stale whitelist entries from accumulating after files
    are moved or deleted.
    """
    missing: list[str] = []
    for prefix in WHITELIST_PREFIXES:
        # For directory prefixes (ending with /), check the parent exists
        # For file paths, check the file or directory exists
        check_path = PROJECT_ROOT / prefix.rstrip("/")
        if not check_path.exists():
            missing.append(prefix)

    if missing:
        raise AssertionError(
            "Whitelist prefixes point to non-existent paths "
            "(clean up stale entries):\n  " + "\n  ".join(missing)
        )

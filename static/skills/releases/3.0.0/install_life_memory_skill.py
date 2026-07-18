#!/usr/bin/env python3
"""Verified installer for the Life Memory MCP Skill V3.

The default smoke is read-only.  A RawEvent roundtrip requires --write-test.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


# The release manifest location is private deployment configuration, not public
# source metadata. Supply it explicitly or through the local environment.
DEFAULT_MANIFEST_URL = os.environ.get("LIFE_MEMORY_MANIFEST_URL", "").strip()
# The package is public; the deployment endpoint remains operator-provided.
DEFAULT_API_URL = os.environ.get("LIFE_MEMORY_API_URL", "").strip()
PACKAGE_NAME = "life-memory-mcp-connect-skill"
PACKAGE_ROOT = "life-memory-mcp-connect"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _download_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"Accept": "application/json, application/zip, text/x-python"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest_url(url: str) -> tuple[str, dict[str, Any]]:
    latest = json.loads(_download_bytes(url).decode("utf-8"))
    relative_manifest = latest.get("manifest")
    version = latest.get("skill_version")
    if not isinstance(relative_manifest, str) or not isinstance(version, str):
        raise RuntimeError("Invalid latest skill manifest pointer")
    base = url.rsplit("/", 1)[0]
    manifest_url = relative_manifest if relative_manifest.startswith("https://") else f"{base}/{relative_manifest.lstrip('/')}"
    manifest = json.loads(_download_bytes(manifest_url).decode("utf-8"))
    if manifest.get("skill_version") != version:
        raise RuntimeError("Skill manifest version mismatch")
    return manifest_url, manifest


def _artifact(manifest: dict[str, Any], name: str) -> dict[str, Any]:
    for artifact in manifest.get("artifacts", []):
        if artifact.get("name") == name:
            return artifact
    raise RuntimeError(f"Manifest does not contain {name}")


def _safe_target(target_dir: Path) -> Path:
    target = target_dir.expanduser().resolve()
    forbidden = {Path(target.anchor).resolve(), Path.home().resolve()}
    if target in forbidden or target.name != PACKAGE_ROOT:
        raise RuntimeError(f"Refusing unsafe target directory: {target}")
    return target


def _validated_members(payload: bytes) -> list[zipfile.ZipInfo]:
    with zipfile.ZipFile(__import__("io").BytesIO(payload)) as archive:
        members = archive.infolist()
        if not members:
            raise RuntimeError("Skill ZIP is empty")
        for member in members:
            path = PurePosixPath(member.filename)
            mode = member.external_attr >> 16
            if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != PACKAGE_ROOT:
                raise RuntimeError("Unsafe ZIP entry rejected")
            if mode and (mode & 0o170000) == 0o120000:
                raise RuntimeError("Symlink ZIP entry rejected")
        return members


def _atomic_extract(payload: bytes, target: Path, force: bool) -> Path:
    _validated_members(payload)
    if target.exists() and not force:
        raise RuntimeError(f"Target already exists: {target}. Use --force to replace this Skill directory.")
    staging_parent = target.parent
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{PACKAGE_ROOT}-", dir=staging_parent))
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(payload)) as archive:
            archive.extractall(staging)
        extracted = staging / PACKAGE_ROOT
        if not (extracted / "SKILL.md").is_file():
            raise RuntimeError("Verified ZIP did not contain the expected Skill root")
        if target.exists():
            shutil.rmtree(target)
        os.replace(extracted, target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return target


def _write_mcp_json(configure_module: Any, api_url: str, agent_id: str, token: str, server_path: Path, output: Path) -> str:
    config = configure_module.build_config(api_url, agent_id, token, server_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(output.resolve())


def main() -> None:
    parser = argparse.ArgumentParser(description="Download, verify, install, bootstrap, and smoke-test Life Memory MCP Skill V3.")
    parser.add_argument("--manifest-url", default=DEFAULT_MANIFEST_URL)
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--target-dir", default=PACKAGE_ROOT)
    parser.add_argument("--project-id", default="life-memory-system")
    parser.add_argument("--agent-name")
    parser.add_argument("--force", action="store_true", help="Replace only an existing life-memory-mcp-connect target directory.")
    parser.add_argument("--write-test", action="store_true", help="Append one low-risk RawEvent roundtrip during smoke.")
    parser.add_argument("--mcp-json-output", help="Private local MCP config path. Omit to avoid writing a token to disk.")
    parser.add_argument("--write-codex-config", action="store_true")
    parser.add_argument("--codex-config-path", default=str(Path.home() / ".codex" / "config.toml"))
    args = parser.parse_args()
    if not args.manifest_url:
        parser.error("--manifest-url is required (or set LIFE_MEMORY_MANIFEST_URL privately)")
    if not args.api_url:
        parser.error("--api-url is required (or set LIFE_MEMORY_API_URL privately)")

    target_dir = _safe_target(Path(args.target_dir))
    manifest_url, manifest = _manifest_url(args.manifest_url)
    artifact = _artifact(manifest, PACKAGE_NAME)
    artifact_url = f"{manifest_url.rsplit('/', 1)[0]}/{Path(str(artifact['path'])).name}"
    payload = _download_bytes(artifact_url)
    if _sha256(payload) != artifact.get("sha256"):
        raise RuntimeError("Downloaded Skill ZIP SHA256 does not match manifest")
    _validated_members(payload)
    skill_dir = _atomic_extract(payload, target_dir, args.force)

    scripts_dir = skill_dir / "scripts"
    server_path = scripts_dir / "life_memory_mcp_server.py"
    bootstrap = _load_module("life_memory_bootstrap", scripts_dir / "bootstrap_life_memory_agent.py")
    configure = _load_module("life_memory_configure", scripts_dir / "configure_mcp.py")
    writer = _load_module("life_memory_codex_writer", scripts_dir / "write_codex_mcp_config.py")

    agent = bootstrap.create_agent(
        args.api_url,
        args.agent_name or f"auto-mcp-agent-install-{os.getpid()}",
        args.project_id,
    )
    agent_id = agent["agent_id"]
    token = agent["api_token"]
    smoke = bootstrap.run_smoke(args.api_url, agent_id, token, args.project_id, args.write_test)

    mcp_json_path = None
    if args.mcp_json_output:
        mcp_json_path = _write_mcp_json(configure, args.api_url, agent_id, token, server_path.resolve(), Path(args.mcp_json_output).expanduser())

    codex_config_path = None
    if args.write_codex_config:
        config_path = Path(args.codex_config_path).expanduser()
        old_text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
        block = writer._block(args.api_url, agent_id, token, server_path.resolve())
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(writer._upsert_block(old_text, block), encoding="utf-8")
        codex_config_path = str(config_path.resolve())

    print(json.dumps({
        "skill_version": manifest["skill_version"],
        "skill_dir": str(skill_dir),
        "manifest_url": manifest_url,
        "api_url": args.api_url.rstrip("/"),
        "agent_id": agent_id,
        "token": "<created-not-printed>",
        "mcp_json_output": mcp_json_path,
        "codex_config_path": codex_config_path,
        "codex_restart_required": bool(args.write_codex_config),
        "smoke": smoke,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

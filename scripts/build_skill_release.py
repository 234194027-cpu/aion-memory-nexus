#!/usr/bin/env python3
"""Build deterministic, signed-by-hash Life Memory Skill release artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SKILL_SOURCES = {
    "life-memory-mcp-connect-skill": ROOT / "skills" / "life-memory-mcp-connect",
    "life-memory-media-ingestion-skill": ROOT / "skills" / "life-memory-media-ingestion",
}
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".git"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_files(source: Path) -> list[Path]:
    files: list[Path] = []
    for path in source.rglob("*"):
        if not path.is_file() or EXCLUDED_PARTS.intersection(path.parts):
            continue
        if path.suffix.lower() in EXCLUDED_SUFFIXES or path.name.startswith(".env"):
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(source).as_posix())


def _zip_timestamp() -> tuple[int, int, int, int, int, int]:
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "315532800"))  # 1980-01-01
    import datetime

    return datetime.datetime.fromtimestamp(max(epoch, 315532800), tz=datetime.timezone.utc).timetuple()[:6]


def _build_zip(source: Path, package_name: str, destination: Path) -> list[str]:
    entries: list[str] = []
    timestamp = _zip_timestamp()
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source_file in _source_files(source):
            relative = source_file.relative_to(source).as_posix()
            arcname = f"{package_name}/{relative}"
            info = zipfile.ZipInfo(arcname, date_time=timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source_file.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
            entries.append(arcname)
    return entries


def _contract() -> dict[str, Any]:
    sys.path.insert(0, str(ROOT))
    from src.platform.mcp.server import TOOL_DEFS, memory_access_map

    return {
        "skill_version": "3.0.0",
        "tool_definitions": TOOL_DEFS,
        "memory_access_map": memory_access_map(),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build(version: str, output_root: Path) -> dict[str, Any]:
    if version != "3.0.0":
        raise ValueError("This repository currently publishes only Skill version 3.0.0")
    output_root = output_root.resolve()
    if output_root in {output_root.anchor and Path(output_root.anchor), ROOT.resolve()}:
        raise ValueError("Refusing a broad Skill release output directory")
    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)

    contract_path = SKILL_SOURCES["life-memory-mcp-connect-skill"] / "scripts" / "mcp_contract.json"
    _write_json(contract_path, _contract())

    release_dir = output_root / "releases" / version
    release_dir.mkdir(parents=True)
    artifacts: list[dict[str, Any]] = []
    for package_name, source in SKILL_SOURCES.items():
        zip_path = release_dir / f"{package_name}.zip"
        entries = _build_zip(source, package_name.removesuffix("-skill"), zip_path)
        artifacts.append(
            {
                "name": package_name,
                "path": f"releases/{version}/{zip_path.name}",
                "size": zip_path.stat().st_size,
                "sha256": _sha256(zip_path),
                "files": entries,
            }
        )

    installer_source = SKILL_SOURCES["life-memory-mcp-connect-skill"] / "scripts" / "install_life_memory_skill.py"
    installer_path = release_dir / "install_life_memory_skill.py"
    shutil.copyfile(installer_source, installer_path)
    artifacts.append(
        {
            "name": "install_life_memory_skill",
            "path": f"releases/{version}/{installer_path.name}",
            "size": installer_path.stat().st_size,
            "sha256": _sha256(installer_path),
            "files": [installer_path.name],
        }
    )
    manifest = {
        "schema_version": 1,
        "skill_version": version,
        "build_commit": os.environ.get("GIT_COMMIT", "local-uncommitted"),
        "artifacts": artifacts,
    }
    _write_json(release_dir / "manifest.json", manifest)
    _write_json(output_root / "latest.json", {"skill_version": version, "manifest": f"releases/{version}/manifest.json"})

    # Stable aliases preserve existing setup URLs while latest.json remains
    # the canonical installer discovery path.
    for artifact in artifacts:
        if artifact["name"].endswith("-skill") or artifact["name"] == "install_life_memory_skill":
            shutil.copyfile(release_dir / Path(artifact["path"]).name, output_root / Path(artifact["path"]).name)
    shutil.copyfile(release_dir / "manifest.json", output_root / "manifest.json")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build deterministic Life Memory Skill V3 release files.")
    parser.add_argument("--version", default="3.0.0")
    parser.add_argument("--output-root", default=str(ROOT / "static" / "skills"))
    args = parser.parse_args()
    manifest = build(args.version, Path(args.output_root).resolve())
    print(json.dumps({"skill_version": args.version, "artifacts": manifest["artifacts"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Validate and clean memory-sync payloads before calling Life Memory MCP/API."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from typing import Any


IDENTIFIER_FIELDS = ("project_id", "repo_id", "workspace_id", "external_id")
MOJIBAKE_MARKERS = ("Ã", "Â", "�")


def _ascii_slug(value: Any, prefix: str) -> str:
    text = str(value or "").strip()
    ascii_text = text.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9_.-]+", "-", ascii_text).strip("-._")
    if not slug:
        digest = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]
        slug = f"{prefix}-{digest}"
    return slug[:128]


def _looks_risky_identifier(value: Any) -> bool:
    if value is None:
        return False
    text = str(value)
    if any(marker in text for marker in MOJIBAKE_MARKERS):
        return True
    if "\\" in text or "/" in text:
        return True
    if re.search(r"^[A-Za-z]:", text):
        return True
    try:
        text.encode("ascii")
    except UnicodeEncodeError:
        return True
    return False


def clean_memory_item(item: dict[str, Any], index: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cleaned = dict(item)
    metadata = dict(cleaned.get("metadata") or {})
    warnings: list[dict[str, Any]] = []

    for field in IDENTIFIER_FIELDS:
        value = cleaned.get(field)
        if _looks_risky_identifier(value):
            original = str(value)
            cleaned[field] = _ascii_slug(value, field)
            metadata[f"original_{field}"] = original
            warnings.append(
                {
                    "index": index,
                    "field": field,
                    "reason": "sanitized_risky_identifier",
                    "sanitized": cleaned[field],
                }
            )

    if not cleaned.get("external_id"):
        content = str(cleaned.get("content") or "")
        digest = hashlib.sha1(content.encode("utf-8", "replace")).hexdigest()[:16]
        cleaned["external_id"] = f"memory-{digest}"
        warnings.append({"index": index, "field": "external_id", "reason": "generated_missing_external_id"})

    if metadata:
        cleaned["metadata"] = metadata
    return cleaned, warnings


def clean_payload(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(payload)
    warnings: list[dict[str, Any]] = []

    for field in ("source_name", "default_project_id"):
        value = cleaned.get(field)
        if _looks_risky_identifier(value):
            original = str(value)
            cleaned[field] = _ascii_slug(value, field)
            warnings.append(
                {
                    "field": field,
                    "reason": "sanitized_risky_identifier",
                    "original": original,
                    "sanitized": cleaned[field],
                }
            )

    memories = []
    for index, item in enumerate(cleaned.get("memories") or []):
        if not isinstance(item, dict):
            warnings.append({"index": index, "reason": "item_not_object"})
            continue
        item_cleaned, item_warnings = clean_memory_item(item, index)
        memories.append(item_cleaned)
        warnings.extend(item_warnings)

    cleaned["memories"] = memories
    cleaned["validation_warnings"] = warnings
    return cleaned


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and clean a Life Memory memory-sync JSON payload.")
    parser.add_argument("payload", nargs="?", help="JSON file path. Reads stdin when omitted.")
    parser.add_argument("--output", help="Optional output JSON path.")
    args = parser.parse_args()

    text = open(args.payload, "r", encoding="utf-8").read() if args.payload else sys.stdin.read()
    payload = json.loads(text)
    cleaned = clean_payload(payload)
    output = json.dumps(cleaned, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(output + "\n")
    else:
        print(output)


if __name__ == "__main__":
    main()

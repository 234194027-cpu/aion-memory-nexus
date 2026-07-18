from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request


FORBIDDEN_MEDIA_RESPONSE_KEYS = {
    "storage_path",
    "wecom_media_id",
    "download_url",
    "download_urls",
    "file_url",
    "local_path",
    "extracted_text_path",
    "extracted_json_path",
}


def _request(
    base_url: str,
    method: str,
    path: str,
    token: str | None = None,
    payload: dict | None = None,
    bearer_token: str | None = None,
) -> tuple[int, dict]:
    url = base_url.rstrip("/") + path
    data = None
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Agent-Token"] = token
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {"raw": body[:500]}
        return exc.code, parsed


def _print_result(name: str, ok: bool, detail: str) -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}: {detail}")


def _artifact_status_ok(artifact: dict, expected_media_type: str) -> bool:
    return (
        artifact.get("media_type") == expected_media_type
        and artifact.get("status") in {"received", "downloaded", "extracted"}
        and bool(artifact.get("id"))
    )


def _find_forbidden_response_keys(payload, *, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            child_path = f"{path}.{key}"
            if key in FORBIDDEN_MEDIA_RESPONSE_KEYS:
                found.append(child_path)
            found.extend(_find_forbidden_response_keys(value, path=child_path))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            found.extend(_find_forbidden_response_keys(value, path=f"{path}[{index}]"))
    return found


def _payload_is_safe(payload: dict) -> tuple[bool, str]:
    forbidden = _find_forbidden_response_keys(payload)
    if forbidden:
        return False, "forbidden_keys=" + ",".join(forbidden[:10])
    return True, "no forbidden media response keys"


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test media note ingestion endpoints without printing secrets.")
    parser.add_argument("--base-url", default=os.getenv("LIFE_MEMORY_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--create-link", help="Optional URL to create as a media artifact. Omitted means read-only smoke.")
    parser.add_argument("--upload-text", help="Optional small text note to upload through /api/media/upload-base64.")
    parser.add_argument("--upload-filename", default="smoke-note.txt", help="Filename for --upload-text. Defaults to smoke-note.txt.")
    parser.add_argument("--extract", action="store_true", help="Ask the server to extract created media. Without --sync-extract this queues extraction.")
    parser.add_argument("--sync-extract", action="store_true", help="Run extraction synchronously for created link/text smoke items.")
    parser.add_argument("--check-wecom-debug", action="store_true", help="Also check /api/wecom/media-debug/events with LIFE_MEMORY_JWT_TOKEN.")
    args = parser.parse_args()

    token = os.getenv("LIFE_MEMORY_AGENT_TOKEN") or os.getenv("LIFE_MEMORY_SYSTEM_API_TOKEN")
    jwt_token = os.getenv("LIFE_MEMORY_JWT_TOKEN")
    failures = 0

    status, health = _request(args.base_url, "GET", "/health")
    health_ok = status == 200 and health.get("status") in {"healthy", "degraded"}
    _print_result("health", health_ok, f"status={status}, app_status={health.get('status')}")
    failures += 0 if health_ok else 1

    if not token:
        if args.create_link or args.upload_text:
            _print_result("media-auth", False, "LIFE_MEMORY_AGENT_TOKEN is required for write smoke")
            failures += 1
        else:
            _print_result("media-list", True, "skipped because LIFE_MEMORY_AGENT_TOKEN is not set")
    else:
        status, artifacts = _request(args.base_url, "GET", "/api/media/artifacts?limit=5", token=token)
        items = artifacts.get("items") if isinstance(artifacts.get("items"), list) else []
        list_ok = status == 200 and isinstance(artifacts.get("items"), list)
        _print_result("media-list", list_ok, f"status={status}, count={len(artifacts.get('items', [])) if isinstance(artifacts.get('items'), list) else 'n/a'}")
        failures += 0 if list_ok else 1

        if list_ok:
            safe_ok, safe_detail = _payload_is_safe(artifacts)
            _print_result("media-list-safe-payload", safe_ok, safe_detail)
            failures += 0 if safe_ok else 1
            if items:
                first_id = items[0].get("id")
                if first_id:
                    detail_status, detail_payload = _request(args.base_url, "GET", f"/api/media/artifacts/{first_id}", token=token)
                    detail_ok = detail_status == 200 and isinstance(detail_payload, dict)
                    _print_result("media-detail", detail_ok, f"status={detail_status}, artifact_id={first_id}")
                    failures += 0 if detail_ok else 1
                    if detail_ok:
                        detail_safe_ok, detail_safe_detail = _payload_is_safe(detail_payload)
                        _print_result("media-detail-safe-payload", detail_safe_ok, detail_safe_detail)
                        failures += 0 if detail_safe_ok else 1

        should_extract = bool(args.extract or args.sync_extract)
        if args.create_link:
            payload = {
                "url": args.create_link,
                "source_text": f"production smoke link: {args.create_link}",
                "source_channel": "smoke",
                "extract": should_extract,
                "sync": bool(args.sync_extract),
            }
            status, created = _request(args.base_url, "POST", "/api/media/link", token=token, payload=payload)
            artifact = created.get("artifact") if isinstance(created.get("artifact"), dict) else {}
            create_ok = status == 200 and _artifact_status_ok(artifact, "link")
            if args.sync_extract:
                create_ok = create_ok and bool(created.get("memory_id")) and artifact.get("status") == "extracted"
            _print_result(
                "media-create-link",
                create_ok,
                f"status={status}, artifact_id={artifact.get('id')}, memory_id={created.get('memory_id')}",
            )
            failures += 0 if create_ok else 1

        if args.upload_text:
            encoded = base64.b64encode(args.upload_text.encode("utf-8")).decode("ascii")
            payload = {
                "filename": args.upload_filename,
                "content_base64": encoded,
                "mime_type": "text/plain",
                "media_type": "file",
                "source_channel": "smoke",
                "extract": should_extract,
                "sync": bool(args.sync_extract),
            }
            status, uploaded = _request(args.base_url, "POST", "/api/media/upload-base64", token=token, payload=payload)
            artifact = uploaded.get("artifact") if isinstance(uploaded.get("artifact"), dict) else {}
            upload_ok = status == 200 and _artifact_status_ok(artifact, "file")
            if args.sync_extract:
                upload_ok = upload_ok and bool(uploaded.get("memory_id")) and artifact.get("status") == "extracted"
            _print_result(
                "media-upload-text",
                upload_ok,
                f"status={status}, artifact_id={artifact.get('id')}, memory_id={uploaded.get('memory_id')}",
            )
            failures += 0 if upload_ok else 1

    if args.check_wecom_debug:
        if not jwt_token:
            _print_result("wecom-media-debug-auth", False, "LIFE_MEMORY_JWT_TOKEN is required for --check-wecom-debug")
            failures += 1
        else:
            status, debug_payload = _request(
                args.base_url,
                "GET",
                "/api/wecom/media-debug/events?limit=5",
                bearer_token=jwt_token,
            )
            debug_ok = status == 200 and isinstance(debug_payload.get("items"), list)
            _print_result(
                "wecom-media-debug",
                debug_ok,
                f"status={status}, count={len(debug_payload.get('items', [])) if isinstance(debug_payload.get('items'), list) else 'n/a'}",
            )
            failures += 0 if debug_ok else 1
            if debug_ok:
                debug_safe_ok, debug_safe_detail = _payload_is_safe(debug_payload)
                _print_result("wecom-media-debug-safe-payload", debug_safe_ok, debug_safe_detail)
                failures += 0 if debug_safe_ok else 1

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

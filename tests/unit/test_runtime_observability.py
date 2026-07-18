import asyncio
import re

from src.shared.utils.request_id import REQUEST_ID_HEADER, RequestIDMiddleware
from src.shared.utils.runtime_metrics import RuntimeMetrics


def test_runtime_metrics_use_safe_labels_and_never_store_payloads() -> None:
    metrics = RuntimeMetrics()
    metrics.record_request(0.1)
    metrics.record_external_call("llm_generate")
    metrics.record_external_call('bad"label\nsecret', failed=True)
    metrics.record_task("memory_extraction", failed=True)

    rendered = metrics.format_prometheus()
    assert 'operation="llm_generate"' in rendered
    assert 'operation="other"' in rendered
    assert "secret" not in rendered
    assert "request_duration_seconds 0.1" in rendered


def test_invalid_inbound_request_id_is_replaced_before_response() -> None:
    async def run() -> None:
        sent = []

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        middleware = RequestIDMiddleware(app)
        await middleware(
            {"type": "http", "headers": [(REQUEST_ID_HEADER.encode(), b"bad value with spaces")]},
            receive,
            send,
        )
        headers = dict(sent[0]["headers"])
        response_id = headers[REQUEST_ID_HEADER.encode()].decode()
        assert re.fullmatch(r"[a-f0-9]{32}", response_id)

    asyncio.run(run())


def test_wecom_disconnected_send_emits_only_safe_failure_metric(monkeypatch) -> None:
    from src.platform.channels import wecom

    metrics = RuntimeMetrics()
    monkeypatch.setattr(wecom, "runtime_metrics", metrics)
    client = wecom.WeComBotClient("bot-id", "secret-value")

    result = asyncio.run(client.send_text_message("recipient-id", "private message body"))

    assert result["errmsg"] == "websocket_not_connected"
    rendered = metrics.format_prometheus()
    assert 'operation="wecom_send"' in rendered
    assert "private message body" not in rendered
    assert "secret-value" not in rendered


def test_health_exposes_safe_operational_component_states() -> None:
    from src.app import main as app_main

    result = asyncio.run(app_main.health_check())

    assert {"wecom", "vector_store", "migrations"}.issubset(result["components"])

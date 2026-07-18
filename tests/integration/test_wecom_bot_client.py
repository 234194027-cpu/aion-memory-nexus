"""企业微信机器人客户端（WeComBotClient）测试。

覆盖长连接客户端的初始化与状态查询行为。
"""


def test_bot_client_init():
    from src.platform.channels.wecom import WeComBotClient

    bot = WeComBotClient(bot_id="test_bot_id", secret="test_secret")
    status = bot.get_status()

    assert status["bot_id_configured"] is True
    assert status["secret_configured"] is True
    assert status["connected"] is False
    assert status["running"] is False


def test_subscription_error_is_safe_and_actionable():
    from src.platform.channels.wecom import _subscription_error

    assert _subscription_error({"errcode": 40001, "errmsg": "invalid credential"}) == (
        "Subscription failed: errcode=40001, errmsg=invalid credential"
    )
    assert _subscription_error("unexpected") == "Subscription failed: invalid response body"


def test_safe_wecom_error_removes_newlines_and_limits_length():
    from src.platform.channels.wecom import _safe_wecom_error

    error = RuntimeError("connection failed\nplease retry " + "x" * 400)
    message = _safe_wecom_error(error)

    assert message.startswith("RuntimeError: connection failed please retry")
    assert "\n" not in message
    assert len(message) <= len("RuntimeError: ") + 300


def test_wecom_uses_application_heartbeat_instead_of_websocket_ping():
    from src.platform.channels.wecom import WeComBotClient

    assert WeComBotClient.CMD_HEARTBEAT == "ping"
    assert WeComBotClient.HEARTBEAT_INTERVAL_SECONDS == 30


def test_connect_disables_generic_websocket_ping(monkeypatch):
    import asyncio

    from src.platform.channels import wecom

    seen_options = {}

    class FakeSocket:
        closed = False

        async def send(self, _payload):
            return None

        async def recv(self):
            return '{"cmd":"aibot_subscribe_reply","body":{"errcode":0}}'

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(3600)
            raise StopAsyncIteration

        async def close(self):
            self.closed = True

    async def fake_connect(*_args, **kwargs):
        seen_options.update(kwargs)
        return FakeSocket()

    async def run():
        monkeypatch.setattr(wecom.websockets, "connect", fake_connect)
        bot = wecom.WeComBotClient("bot", "secret")
        await bot.connect()
        assert bot.is_connected()
        await bot.disconnect()

    asyncio.run(run())
    assert seen_options["ping_interval"] is None
    assert seen_options["ping_timeout"] is None


def test_enter_chat_event_sends_welcome_without_calling_conversation_handler():
    import asyncio
    import json

    from src.platform.channels.wecom import WeComBotClient

    class FakeSocket:
        closed = False

        def __init__(self):
            self.sent = []
            self._frames = iter([
                json.dumps({
                    "cmd": "aibot_event_callback",
                    "headers": {"req_id": "enter-chat-request"},
                    "body": {
                        "msgid": "event-1",
                        "aibotid": "bot",
                        "msgtype": "event",
                        "event": {"eventtype": "enter_chat"},
                    },
                }),
            ])

        async def send(self, payload):
            self.sent.append(json.loads(payload))

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._frames)
            except StopIteration:
                raise StopAsyncIteration

    async def run():
        bot = WeComBotClient("bot", "secret")
        socket = FakeSocket()
        handler_called = False

        async def handler(_message):
            nonlocal handler_called
            handler_called = True
            return "should not be called for an event"

        bot.set_message_handler(handler)
        bot._ws = socket
        bot._running = True
        bot._connected = True
        await bot._receive_loop()

        assert handler_called is False
        assert socket.sent == [{
            "cmd": "aibot_respond_welcome_msg",
            "headers": {"req_id": "enter-chat-request"},
            "body": {
                "msgtype": "text",
                "text": {"content": bot.DEFAULT_WELCOME_TEXT},
            },
        }]

    asyncio.run(run())


def test_protocol_success_ack_is_not_reported_as_unhandled(caplog):
    import asyncio
    import json
    import logging

    from src.platform.channels.wecom import WeComBotClient

    class FakeSocket:
        def __init__(self):
            self._frames = iter([
                json.dumps({
                    "headers": {},
                    "errcode": 0,
                    "errmsg": "ok",
                }),
            ])

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._frames)
            except StopIteration:
                raise StopAsyncIteration

    async def run():
        bot = WeComBotClient("bot", "secret")
        bot._ws = FakeSocket()
        bot._running = True
        bot._connected = True
        await bot._receive_loop()

    with caplog.at_level(logging.WARNING, logger="src.platform.channels.wecom"):
        asyncio.run(run())

    assert not any("WeCom unhandled frame" in record.message for record in caplog.records)

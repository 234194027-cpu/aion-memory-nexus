"""Enterprise WeChat now routes normal text through one conversation Agent."""

import asyncio

from src.execution.runtime.conversation_agent import ConversationAnswer
from src.platform.channels.wecom import WeComBotMessage
from src.shared.db.database import init_db


def _message(content: str, message_id: str) -> WeComBotMessage:
    return WeComBotMessage(
        {
            "msgtype": "text",
            "from_userid": "conversation-ledger-user",
            "chatid": "conversation-ledger-chat",
            "chattype": "single",
            "msgid": message_id,
            "text": {"content": content},
        }
    )


def test_wecom_free_text_modes_and_natural_answers_share_one_runtime(monkeypatch):
    from src.platform.channels import wecom_handlers

    calls: list[dict] = []

    async def answer(_db, **kwargs):
        calls.append(kwargs)
        return ConversationAnswer(
            text=f"自然回复：{kwargs['message']}",
            run_id=f"run-{len(calls)}",
            response_mode="ANSWER",
            confidence="HIGH",
            citations=(),
            created_event_ids=(),
            turn_id=f"turn-{len(calls)}",
            session_id="session-ledger",
        )

    monkeypatch.setattr(wecom_handlers, "run_conversational_turn", answer)

    async def run():
        await init_db()
        replies = [
            await wecom_handlers.handle_wecom_message(_message("你好", "ledger-1")),
            await wecom_handlers.handle_wecom_message(_message("提问模式", "ledger-2")),
            await wecom_handlers.handle_wecom_message(
                _message("大连和河北高碑店", "ledger-3")
            ),
        ]
        assert replies == [
            "自然回复：你好",
            "自然回复：提问模式",
            "自然回复：大连和河北高碑店",
        ]
        assert [call["message_id"] for call in calls] == [
            "ledger-1",
            "ledger-2",
            "ledger-3",
        ]
        assert all(call["channel"] == "wecom" for call in calls)
        assert len({call["channel_session_key"] for call in calls}) == 1

    asyncio.run(run())


def test_wecom_help_explains_there_is_no_mode_or_answer_prefix():
    async def run():
        reply = await __import__(
            "src.platform.channels.wecom_handlers",
            fromlist=["handle_wecom_message"],
        ).handle_wecom_message(_message("/help", "ledger-help"))
        assert "不需要切换聊天模式或提问模式" in reply
        assert "不需要用“回答：”开头" in reply

    asyncio.run(run())

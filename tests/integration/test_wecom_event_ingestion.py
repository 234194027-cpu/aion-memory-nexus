"""企业微信事件摄取（wecom_event_ingestion）测试。

覆盖事件分类（task/noise/preference/短事件）、质量评估、确认回复、
摄取指令解析（撤回/不要记/分组/单独记/补充）、待处理指令存储与应用、
分组摘要、批量质量指标等控制流程。
"""


def test_wecom_event_ingestion_classification_and_controls():
    from src.platform.services.wecom_event_ingestion import (
        DO_NOT_REMEMBER_COMMANDS,
        FORCE_GROUP_COMMANDS,
        PENDING_INGEST_DIRECTIVE_KEY,
        STANDALONE_COMMANDS,
        UNDO_LAST_COMMANDS,
        _extract_replacement_text,
        apply_pending_ingest_directive,
        assess_ingest_quality,
        build_ingest_reply,
        build_ingest_quality_metrics,
        classify_wecom_event,
        confirmation_reply,
        get_ingest_preferences,
        parse_ingest_directive,
        store_pending_ingest_directive,
        summarize_event_group,
        WeComIngestResult,
    )
    from types import SimpleNamespace

    task = classify_wecom_event("明天要跟进客户方案")
    assert task.kind == "task"
    assert task.should_store is True
    task_quality = assess_ingest_quality(content="需要跟进客户方案", classification=task)
    assert task_quality.score < 0.8
    assert "due_time" in task_quality.missing

    noise = classify_wecom_event("hi")
    assert noise.kind == "noise"
    assert noise.should_store is False
    assert "不进正式记忆库" in confirmation_reply(noise)

    preference = classify_wecom_event("我希望以后问题短一点")
    assert preference.kind == "preference"

    short_event = classify_wecom_event("开会")
    assert short_event.needs_follow_up is True
    assert short_event.follow_up_question

    assert "撤回上一条" in UNDO_LAST_COMMANDS
    assert "不要记" in DO_NOT_REMEMBER_COMMANDS
    assert "这条和刚才是一件事" in FORCE_GROUP_COMMANDS
    assert "这条单独记" in STANDALONE_COMMANDS
    assert _extract_replacement_text("把上一条改成: 今天和张三确认上线计划") == "今天和张三确认上线计划"
    directive = parse_ingest_directive("补充上一条：张三是客户")
    assert directive.force_group is True
    assert directive.content == "张三是客户"
    assert parse_ingest_directive("这条单独记：新的事件").standalone is True

    contact_for_pending = SimpleNamespace(contact_metadata={})
    store_pending_ingest_directive(contact_for_pending, parse_ingest_directive("这条单独记"))
    assert contact_for_pending.contact_metadata[PENDING_INGEST_DIRECTIVE_KEY]["standalone"] is True
    applied = apply_pending_ingest_directive(contact_for_pending, parse_ingest_directive("新的事件"))
    assert applied.content == "新的事件"
    assert applied.standalone is True
    assert PENDING_INGEST_DIRECTIVE_KEY not in contact_for_pending.contact_metadata

    reply = build_ingest_reply(WeComIngestResult(event_id="evt_test", classification=short_event, reply="记下了"))
    assert "evt_test" in reply
    assert "顺手确认" in reply
    grouped_reply = build_ingest_reply(
        WeComIngestResult(
            event_id="evt_grouped",
            classification=short_event,
            reply="记下了",
            grouped=True,
            group_summary="第一条；第二条",
        )
    )
    assert "并到刚才那组" in grouped_reply
    assert "组摘要" in grouped_reply

    events = [
        SimpleNamespace(event_metadata={"event_kind": "event", "wecom_ingest_status": "active", "wecom_event_group_id": "g1", "needs_follow_up": True, "quality_score": 0.6}),
        SimpleNamespace(event_metadata={"event_kind": "task", "wecom_ingest_status": "revoked"}),
    ]
    metrics = build_ingest_quality_metrics(events)
    assert metrics["event_count"] == 2
    assert metrics["kind_counts"]["event"] == 1
    assert metrics["grouped_count"] == 1
    assert metrics["follow_up_needed_count"] == 1
    assert metrics["average_quality_score"] == 0.6
    assert metrics["low_quality_count"] == 1

    contact = SimpleNamespace(contact_metadata={"wecom_ingest_preferences": {"message_count": 6, "average_message_length": 8}})
    assert "短句确认" in confirmation_reply(task, contact)
    assert get_ingest_preferences(contact)["message_count"] == 6
    summary = summarize_event_group([
        SimpleNamespace(content="第一条"),
        SimpleNamespace(content="第二条"),
    ])
    assert summary == "第一条；第二条"

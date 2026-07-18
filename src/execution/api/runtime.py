"""Read-only observability endpoints for the dark-launched V2 runtime."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.execution.models.agent_runtime import AgentHandoff, AgentRun, AgentStep
from src.execution.models.memory_work import MemoryWorkCase, MemoryWorkDecision, MemoryWorkEvidence
from src.memory.models.committed_memory import CommittedMemory
from src.memory.models.raw_event import ProcessingStatus, RawEvent
from src.execution.schemas.runtime import (
    AttentionItemResponse,
    AttentionListResponse,
    ConversationCitationEvidence,
    ConversationTurnRequest,
    ConversationTurnResponse,
    ConversationStateResponse,
    RuntimeRunDetailResponse,
    RuntimeRunItem,
    RuntimeRunListResponse,
    RuntimeStatusResponse,
    RuntimeStepItem,
    RuntimeMetricsResponse,
    OpenLoopItemResponse,
    OpenLoopListResponse,
    InsightProposalItem,
    InsightStatusRequest,
    ShadowReportResponse,
)
from src.platform.services.attention_service import AttentionService
from src.cognition.services.open_loops import OpenLoopService
from src.cognition.services.reflection import ReflectionService
from src.cognition.models.insight_proposal import InsightProposal
from src.execution.runtime.conversation_agent import run_conversational_turn
from src.execution.runtime.conversation_ledger import ConversationLedger
from src.execution.runtime.feature_flags import is_runtime_enabled
from src.execution.models.agent_runtime import AgentRole
from src.execution.runtime.shadow_report import build_shadow_report
from src.execution.runtime.metrics import build_runtime_metrics
from src.execution.runtime.workspace import AgentWorkspaceService
from src.shared.config import settings
from src.shared.db.database import get_db
from src.shared.security.dependencies import get_current_user
from src.shared.version import get_runtime_profiles


router = APIRouter()


def _enum_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _run_item(run: AgentRun) -> RuntimeRunItem:
    return RuntimeRunItem(
        id=run.id,
        session_id=run.session_id,
        trigger_type=run.trigger_type,
        trigger_id=run.trigger_id,
        model=run.model,
        status=_enum_value(run.status),
        step_count=run.step_count,
        model_call_count=run.model_call_count,
        tool_call_count=run.tool_call_count,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        cost=run.cost,
        error_code=run.error_code,
        started_at=run.started_at,
        ended_at=run.ended_at,
    )


@router.get("/status", response_model=RuntimeStatusResponse)
async def runtime_status(_user=Depends(get_current_user)):
    """Expose rollout state without exposing provider or prompt configuration."""
    return RuntimeStatusResponse(
        runtime_enabled=settings.AGENT_RUNTIME_ENABLED,
        conversational_enabled=settings.CONVERSATIONAL_AGENT_ENABLED,
        working_shadow_enabled=settings.WORKING_AGENT_SHADOW_ENABLED,
        working_active_enabled=settings.WORKING_AGENT_ACTIVE_ENABLED,
        profiles=get_runtime_profiles(),
    )


@router.get("/runs", response_model=RuntimeRunListResponse)
async def list_runtime_runs(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    rows = list((await db.execute(
        select(AgentRun)
        .where(AgentRun.user_id == user.id)
        .order_by(AgentRun.started_at.desc())
        .limit(limit)
    )).scalars())
    return RuntimeRunListResponse(runs=[_run_item(row) for row in rows], total=len(rows))


@router.get("/shadow-report", response_model=ShadowReportResponse)
async def get_shadow_report(
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    return ShadowReportResponse(**await build_shadow_report(db, user_id=user.id))


@router.post("/conversation/turn", response_model=ConversationTurnResponse)
async def conversational_turn(
    payload: ConversationTurnRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Authenticated web entry for the built-in conversational profile only."""
    if not is_runtime_enabled(AgentRole.CONVERSATIONAL):
        raise HTTPException(status_code=409, detail="conversational runtime is disabled")
    answer = await run_conversational_turn(
        db,
        user_id=user.id,
        channel="web",
        channel_session_key=payload.session_key,
        message=payload.message.strip(),
        message_id=payload.message_id,
    )
    return ConversationTurnResponse(
        text=answer.text,
        run_id=answer.run_id,
        turn_id=answer.turn_id or "",
        session_id=answer.session_id or "",
        response_mode=answer.response_mode,
        confidence=answer.confidence,
        citations=list(answer.citations),
        citation_evidence=[
            ConversationCitationEvidence(
                memory_id=item.memory_id,
                source_event_ids=list(item.source_event_ids),
                epistemic_status=item.epistemic_status,
                valid_from=item.valid_from,
                valid_until=item.valid_until,
            )
            for item in answer.citation_evidence
        ],
    )


@router.get("/conversation/state", response_model=ConversationStateResponse)
async def conversation_state(
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Return bounded conversation state without prompts or transcript text."""
    return ConversationStateResponse(
        **await ConversationLedger(db).conversation_state(user_id=user.id)
    )


@router.delete("/conversation/data")
async def delete_conversation_ledger(
    confirm: str = Query(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Delete ledger, projections and conversation-derived memory sources."""
    if confirm != "DELETE":
        raise HTTPException(status_code=400, detail="confirm=DELETE is required")
    from src.execution.runtime.conversation_deletion import delete_conversation_data

    return {
        "deleted": True,
        "counts": await delete_conversation_data(db, user_id=user.id),
    }


@router.get("/attention", response_model=AttentionListResponse)
async def list_attention_candidates(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Expose pre-gated attention candidates; delivery remains a separate platform action."""
    items = await AttentionService(db).list_candidates(user_id=user.id, limit=limit)
    return AttentionListResponse(items=[
        AttentionItemResponse(
            source_type=item.source_type,
            source_id=item.source_id,
            priority=item.priority,
            prompt=item.prompt,
        )
        for item in items
    ])


@router.get("/metrics", response_model=RuntimeMetricsResponse)
async def get_runtime_metrics(
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    return RuntimeMetricsResponse(**await build_runtime_metrics(db, user_id=user.id))


@router.get("/working/status")
async def working_status(
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    case_rows = (
        await db.execute(
            select(MemoryWorkCase.status, func.count(MemoryWorkCase.id))
            .where(MemoryWorkCase.user_id == user.id)
            .group_by(MemoryWorkCase.status)
        )
    ).all()
    failed_events = await db.scalar(
        select(func.count(RawEvent.id)).where(
            RawEvent.user_id == user.id,
            RawEvent.processing_status == ProcessingStatus.FAILED,
        )
    )
    decision_count = await db.scalar(
        select(func.count(MemoryWorkDecision.id)).where(MemoryWorkDecision.user_id == user.id)
    )
    automatic_memory_count = await db.scalar(
        select(func.count(CommittedMemory.id)).where(
            CommittedMemory.user_id == user.id,
            CommittedMemory.origin_kind == "working_agent",
        )
    )
    retryable_failed_events = await db.scalar(
        select(func.count(RawEvent.id)).where(
            RawEvent.user_id == user.id,
            RawEvent.processing_status == ProcessingStatus.FAILED,
            RawEvent.processing_next_retry_at.is_not(None),
        )
    )
    recent_runs = list(
        (
            await db.execute(
                select(AgentRun)
                .where(
                    AgentRun.user_id == user.id,
                    AgentRun.trigger_type == "raw_event",
                    AgentRun.ended_at.is_not(None),
                )
                .order_by(AgentRun.ended_at.desc())
                .limit(500)
            )
        ).scalars()
    )
    durations_ms = [
        max(0.0, (run.ended_at - run.started_at).total_seconds() * 1000)
        for run in recent_runs
        if run.started_at is not None and run.ended_at is not None
    ]
    counts = {str(status): int(count) for status, count in case_rows}
    return {
        "ledger_version": "memory-case-v2.4",
        "case_counts": counts,
        "active_backlog": sum(
            counts.get(status, 0)
            for status in ("open", "awaiting_evidence", "ready_to_commit", "conflict_review", "failed")
        ),
        "waiting_for_evidence": counts.get("awaiting_evidence", 0),
        "failed_event_count": int(failed_events or 0),
        "retryable_failed_event_count": int(retryable_failed_events or 0),
        "decision_count": int(decision_count or 0),
        "automatic_memory_count": int(automatic_memory_count or 0),
        "average_processing_ms": round(sum(durations_ms) / len(durations_ms), 2)
        if durations_ms
        else None,
        "autonomous_memory_enabled": True,
        "shared_cognition": {
            "formal_memory_retrieval": True,
            "document_source_search": True,
            "unconfirmed_clue_search": True,
            "direct_working_workspace_access": False,
        },
        "conversation_memory_projection": AgentWorkspaceService().conversation_memory_projection_status(
            user_id=user.id
        ),
    }


@router.get("/working/cases")
async def list_working_cases(
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    statement = select(MemoryWorkCase).where(MemoryWorkCase.user_id == user.id)
    if status:
        statement = statement.where(MemoryWorkCase.status == status)
    rows = list(
        (
            await db.execute(
                statement.order_by(MemoryWorkCase.updated_at.desc()).limit(limit)
            )
        ).scalars()
    )
    return {
        "items": [
            {
                "id": item.id,
                "proposition_key": item.proposition_key,
                "case_type": item.case_type,
                "title": item.title,
                "status": item.status,
                "sensitivity": item.sensitivity,
                "confidence": item.confidence,
                "active_memory_id": item.active_memory_id,
                "version": item.version,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
                "resolved_at": item.resolved_at,
            }
            for item in rows
        ],
        "total": len(rows),
    }


@router.get("/working/cases/{case_id}")
async def get_working_case(
    case_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    case = await db.scalar(
        select(MemoryWorkCase).where(
            MemoryWorkCase.id == case_id,
            MemoryWorkCase.user_id == user.id,
        )
    )
    if case is None:
        raise HTTPException(status_code=404, detail="Memory work case not found")
    evidence = list(
        (
            await db.execute(
                select(MemoryWorkEvidence)
                .where(
                    MemoryWorkEvidence.case_id == case.id,
                    MemoryWorkEvidence.user_id == user.id,
                )
                .order_by(MemoryWorkEvidence.created_at.asc())
            )
        ).scalars()
    )
    decisions = list(
        (
            await db.execute(
                select(MemoryWorkDecision)
                .where(
                    MemoryWorkDecision.case_id == case.id,
                    MemoryWorkDecision.user_id == user.id,
                )
                .order_by(MemoryWorkDecision.created_at.asc())
            )
        ).scalars()
    )
    handoffs = list(
        (
            await db.execute(
                select(AgentHandoff)
                .where(
                    AgentHandoff.case_id == case.id,
                    AgentHandoff.user_id == user.id,
                )
                .order_by(AgentHandoff.created_at.asc())
            )
        ).scalars()
    )
    return {
        "id": case.id,
        "proposition_key": case.proposition_key,
        "case_type": case.case_type,
        "title": case.title,
        "summary": case.summary,
        "status": case.status,
        "sensitivity": case.sensitivity,
        "confidence": case.confidence,
        "active_memory_id": case.active_memory_id,
        "version": case.version,
        "metadata": case.case_metadata,
        "created_at": case.created_at,
        "updated_at": case.updated_at,
        "resolved_at": case.resolved_at,
        "evidence": [
            {
                "id": item.id,
                "raw_event_id": item.raw_event_id,
                "source_turn_id": item.source_turn_id,
                "episode_id": item.episode_id,
                "quote": item.quote,
                "relationship": item.relationship,
                "source_type": item.source_type,
                "trust_class": item.trust_class,
                "occurred_at": item.occurred_at,
            }
            for item in evidence
        ],
        "decisions": [
            {
                "id": item.id,
                "source_run_id": item.source_run_id,
                "source_event_id": item.source_event_id,
                "state": item.state,
                "rationale": item.rationale,
                "rationale_codes": item.rationale_codes,
                "duplicate_refs": item.duplicate_refs,
                "conflict_refs": item.conflict_refs,
                "memory_ids": item.memory_ids,
                "policy_result": item.policy_result,
                "model": item.model,
                "prompt_id": item.prompt_id,
                "prompt_version": item.prompt_version,
                "created_at": item.created_at,
            }
            for item in decisions
        ],
        "evidence_requests": [
            {
                "id": item.id,
                "status": _enum_value(item.status),
                "question": item.question,
                "requirements": item.evidence_requirements,
                "resolution_condition": item.resolution_condition,
                "sensitivity_limit": item.sensitivity_limit,
                "attempt_count": item.attempt_count,
                "next_eligible_at": item.next_eligible_at,
                "asked_at": item.asked_at,
                "responded_at": item.responded_at,
                "expires_at": item.expires_at,
            }
            for item in handoffs
        ],
    }


@router.get("/open-loops", response_model=OpenLoopListResponse)
async def list_open_loops(
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    loops = await OpenLoopService(db).list(user_id=user.id, limit=limit)
    return OpenLoopListResponse(items=[
        OpenLoopItemResponse(
            source_type=item.source_type,
            source_id=item.source_id,
            title=item.title,
            next_step=item.next_step,
            priority=item.priority,
            due_at=item.due_at,
        )
        for item in loops
    ])


def _insight_item(item: InsightProposal) -> InsightProposalItem:
    return InsightProposalItem(
        id=item.id,
        title=item.title,
        summary=item.summary,
        support_memory_ids=list(item.support_memory_ids or []),
        counter_memory_ids=list(item.counter_memory_ids or []),
        confidence=float(item.confidence or 0.0),
        invalidation_condition=item.invalidation_condition,
        status=item.status,
    )


@router.get("/insights", response_model=list[InsightProposalItem])
async def list_insights(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    return [_insight_item(item) for item in await ReflectionService(db).list(user_id=user.id)]


@router.post("/insights/refresh", response_model=list[InsightProposalItem])
async def refresh_insights(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    proposals = await ReflectionService(db).refresh(user_id=user.id)
    await db.commit()
    return [_insight_item(item) for item in proposals]


@router.patch("/insights/{insight_id}", response_model=InsightProposalItem)
async def update_insight_status(insight_id: str, payload: InsightStatusRequest, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    insight = (await db.execute(select(InsightProposal).where(InsightProposal.id == insight_id, InsightProposal.user_id == user.id))).scalar_one_or_none()
    if insight is None:
        raise HTTPException(status_code=404, detail="Insight proposal not found")
    try:
        await ReflectionService(db).record_feedback(insight, user_id=user.id, status=payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid insight feedback") from exc
    await db.commit()
    return _insight_item(insight)


@router.get("/runs/{run_id}", response_model=RuntimeRunDetailResponse)
async def get_runtime_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    run = (await db.execute(
        select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == user.id)
    )).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Runtime run not found")
    steps = list((await db.execute(
        select(AgentStep).where(AgentStep.run_id == run.id).order_by(AgentStep.step_no)
    )).scalars())
    return RuntimeRunDetailResponse(
        **_run_item(run).model_dump(),
        steps=[
            RuntimeStepItem(
                step_no=step.step_no,
                step_type=_enum_value(step.step_type),
                tool_name=step.tool_name,
                status=_enum_value(step.status),
                error_code=step.error_code,
                duration_ms=step.duration_ms,
                result_summary=step.result_summary,
            )
            for step in steps
        ],
    )

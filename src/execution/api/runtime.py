"""Read-only observability endpoints for the dark-launched V2 runtime."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.execution.models.agent_runtime import AgentHandoff, AgentRun, AgentStep
from src.execution.models.memory_work import MemoryWorkCase, MemoryWorkDecision, MemoryWorkEvidence
from src.execution.models.memory_operations import EvidenceSeal, MemoryMaintenanceAction, MemoryMaintenanceControl, MemoryMaintenanceRun, UserMemoryBrief
from src.memory.models.committed_memory import CommittedMemory
from src.memory.models.raw_event import ProcessingStatus, RawEvent
from src.memory.models.graph_projection import GraphShadowObservation
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


class MaintenanceControlRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=256)


class MaintenanceRollbackRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=256)


class MaintenanceQualityReportRequest(BaseModel):
    report_id: str = Field(min_length=3, max_length=96)
    metrics: dict


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
    queued_event_count = await db.scalar(
        select(func.count(RawEvent.id)).where(
            RawEvent.user_id == user.id,
            RawEvent.processing_status == ProcessingStatus.QUEUED,
        )
    )
    maintenance_rows = (
        await db.execute(
            select(MemoryMaintenanceAction.action, func.count(MemoryMaintenanceAction.id))
            .where(MemoryMaintenanceAction.user_id == user.id)
            .group_by(MemoryMaintenanceAction.action)
        )
    ).all()
    seal_count = await db.scalar(select(func.count(EvidenceSeal.id)).where(EvidenceSeal.user_id == user.id))
    brief = await db.scalar(select(UserMemoryBrief).where(UserMemoryBrief.user_id == user.id))
    control = await db.scalar(
        select(MemoryMaintenanceControl).where(MemoryMaintenanceControl.user_id == user.id)
    )
    maintenance_run_rows = (
        await db.execute(
            select(MemoryMaintenanceRun.state, func.count(MemoryMaintenanceRun.id))
            .where(or_(MemoryMaintenanceRun.user_id == user.id, MemoryMaintenanceRun.user_id.is_(None)))
            .group_by(MemoryMaintenanceRun.state)
        )
    ).all()
    maintenance_token_used = await db.scalar(
        select(func.coalesce(func.sum(MemoryMaintenanceRun.token_used), 0)).where(
            or_(MemoryMaintenanceRun.user_id == user.id, MemoryMaintenanceRun.user_id.is_(None))
        )
    )
    graph_shadow_count = await db.scalar(
        select(func.count(GraphShadowObservation.id)).where(GraphShadowObservation.user_id == user.id)
    )
    graph_shadow_latency = await db.scalar(
        select(func.avg(GraphShadowObservation.graph_latency_ms)).where(GraphShadowObservation.user_id == user.id)
    )
    graph_shadow_source_coverage = await db.scalar(
        select(func.avg(GraphShadowObservation.source_coverage)).where(GraphShadowObservation.user_id == user.id)
    )
    graph_shadow_novel = await db.scalar(
        select(func.coalesce(func.sum(GraphShadowObservation.novel_verified_count), 0)).where(
            GraphShadowObservation.user_id == user.id
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
        "ledger_version": "memory-operations-v2.5.2",
        "case_counts": counts,
        "active_backlog": sum(
            counts.get(status, 0)
            for status in ("open", "awaiting_evidence", "ready_to_commit", "conflict_review", "failed")
        ),
        "waiting_for_evidence": counts.get("awaiting_evidence", 0),
        "queue_backlog": int(queued_event_count or 0),
        "failed_event_count": int(failed_events or 0),
        "retryable_failed_event_count": int(retryable_failed_events or 0),
        "decision_count": int(decision_count or 0),
        "automatic_memory_count": int(automatic_memory_count or 0),
        "average_processing_ms": round(sum(durations_ms) / len(durations_ms), 2)
        if durations_ms
        else None,
        "autonomous_memory_enabled": True,
        "resource_budget": {
            "daily_model_call_limit": settings.WORKING_AGENT_DAILY_MODEL_CALL_LIMIT,
            "daily_priority_reserve": settings.WORKING_AGENT_DAILY_PRIORITY_RESERVE,
            "daily_maintenance_call_limit": settings.WORKING_AGENT_DAILY_MAINTENANCE_CALL_LIMIT,
            "scan_batch_size": settings.WORKING_AGENT_SCAN_BATCH_SIZE,
            "timezone": settings.WORKING_AGENT_BUDGET_TIMEZONE,
        },
        "maintenance_actions": {str(action): int(count) for action, count in maintenance_rows},
        "maintenance_runs": {str(state): int(count) for state, count in maintenance_run_rows},
        "maintenance_token_used": int(maintenance_token_used or 0),
        "evidence_seal_count": int(seal_count or 0),
        "memory_brief": {
            "generated_at": brief.generated_at if brief is not None else None,
            "memory_count": len(brief.memory_ids or []) if brief is not None else 0,
            "token_estimate": brief.token_estimate if brief is not None else 0,
        },
        "maintenance_control": {
            "state": control.state if control is not None else "active",
            "pause_reason": control.pause_reason if control is not None else None,
            "integrity_fault": bool(control.integrity_fault) if control is not None else False,
            "paused_at": control.paused_at if control is not None else None,
            "resumed_at": control.resumed_at if control is not None else None,
            "updated_at": control.updated_at if control is not None else None,
        },
        "graph_shadow": {
            "enabled": bool(settings.GRAPHITI_ENABLED),
            "shadow_mode": bool(settings.GRAPHITI_SHADOW_MODE),
            "observation_count": int(graph_shadow_count or 0),
            "average_latency_ms": round(float(graph_shadow_latency), 2) if graph_shadow_latency is not None else None,
            "average_source_coverage": round(float(graph_shadow_source_coverage), 4) if graph_shadow_source_coverage is not None else None,
            "novel_verified_count": int(graph_shadow_novel or 0),
            "active_write_authority": False,
        },
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


@router.get("/working/maintenance/actions")
async def list_maintenance_actions(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    rows = list(
        (
            await db.execute(
                select(MemoryMaintenanceAction)
                .where(MemoryMaintenanceAction.user_id == user.id)
                .order_by(MemoryMaintenanceAction.created_at.desc())
                .limit(limit)
            )
        ).scalars()
    )
    return {
        "items": [
            {
                "id": item.id,
                "run_id": item.run_id,
                "action": item.action,
                "state": item.state,
                "input_memory_ids": item.input_memory_ids,
                "input_event_ids": item.input_event_ids,
                "output_memory_id": item.output_memory_id,
                "reason_code": item.reason_code,
                "reversible_until": item.reversible_until,
                "rolled_back_at": item.rolled_back_at,
                "rollback_action_id": item.rollback_action_id,
                "created_at": item.created_at,
            }
            for item in rows
        ],
        "total": len(rows),
    }


@router.get("/working/maintenance/control")
async def get_maintenance_control(
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    from src.execution.services.memory_operations import MemoryOperationsCoordinator

    control = await MemoryOperationsCoordinator(db).get_control(user.id)
    await db.commit()
    return {
        "state": control.state,
        "pause_reason": control.pause_reason,
        "last_error_code": control.last_error_code,
        "integrity_fault": bool(control.integrity_fault),
        "shadow_passes": int(control.shadow_passes or 0),
        "paused_at": control.paused_at,
        "resumed_at": control.resumed_at,
        "updated_at": control.updated_at,
    }


@router.post("/working/maintenance/pause")
async def pause_maintenance(
    payload: MaintenanceControlRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    from src.execution.services.memory_operations import MemoryOperationsCoordinator

    control = await MemoryOperationsCoordinator(db).pause_control(
        user.id,
        reason=payload.reason,
        manual=True,
        actor=user.id,
    )
    await db.commit()
    return {"state": control.state, "pause_reason": control.pause_reason, "paused_at": control.paused_at}


@router.post("/working/maintenance/resume")
async def resume_maintenance(
    payload: MaintenanceControlRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    from src.execution.services.memory_operations import MemoryOperationsCoordinator

    control = await MemoryOperationsCoordinator(db).request_resume(
        user.id,
        actor=user.id,
        reason=payload.reason,
    )
    await db.commit()
    return {"state": control.state, "message": "shadow_validation_required"}


@router.post("/working/maintenance/actions/{action_id}/rollback")
async def rollback_maintenance_action(
    action_id: str,
    payload: MaintenanceRollbackRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    from src.execution.services.memory_operations import MemoryOperationsCoordinator

    try:
        action = await MemoryOperationsCoordinator(db).rollback_action(
            user_id=user.id,
            action_id=action_id,
            actor=user.id,
            reason=payload.reason,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    return {"status": "rolled_back", "rollback_action_id": action.id}


@router.post("/working/maintenance/quality-report")
async def submit_maintenance_quality_report(
    payload: MaintenanceQualityReportRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    from src.execution.services.memory_operations import MemoryOperationsCoordinator

    allowed = {
        "schema", "observation_count", "scenario_count", "context_continuity_rate",
        "memory_hit_rate", "source_coverage", "assistant_fact_leak_rate",
        "wrong_merge_rate", "correction_accuracy", "proactive_relevance",
        "response_p95_ms", "average_working_model_calls",
        "average_tokens_per_retained_memory", "cleanup_safety_rate",
    }
    if set(payload.metrics).difference(allowed):
        raise HTTPException(status_code=400, detail="quality_report_contains_unsupported_fields")
    control = await MemoryOperationsCoordinator(db).apply_quality_report(
        user.id,
        metrics=payload.metrics,
        report_id=payload.report_id,
    )
    await db.commit()
    return {"maintenance_state": control.state, "pause_reason": control.pause_reason}


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
                "evidence_seal_id": item.evidence_seal_id,
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

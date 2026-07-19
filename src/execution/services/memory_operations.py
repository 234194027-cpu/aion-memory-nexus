"""Autonomous maintenance loop for governed Working-Agent memories.

The coordinator is deliberately conservative: it can remove redundant source
material and supersede exact duplicates, but never invents a fact or deletes a
formal memory.  Every mutation receives a durable maintenance action first.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.execution.models.agent_runtime import AgentHandoff, AgentHandoffStatus, AgentRole, AgentRun, AgentSession
from src.execution.models.conversation import ConversationAttentionCandidate
from src.execution.models.memory_operations import (
    EvidenceSeal,
    MemoryMaintenanceAction,
    MemoryMaintenanceRun,
    UserMemoryBrief,
)
from src.execution.models.memory_relation import MemoryRelation
from src.execution.models.memory_work import MemoryWorkCase, MemoryWorkEvidence
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.memory.models.memory_source import MemorySource
from src.memory.models.raw_event import ProcessingStatus, RawEvent, SensitivityLevel
from src.memory.services.deduplicator import MemoryDeduplicator
from src.shared.ids.id_generator import generate_id


UTC = timezone.utc
NOISE_MARKERS = ("测试", "test", "hello", "hi", "哈哈", "哈哈哈", "ok", "好的", "收到")
EXPLICIT_MARKERS = ("请记住", "帮我记住", "纠正", "改成", "我计划", "我准备", "截止", "承诺")
SENSITIVE_VALUES = {SensitivityLevel.PRIVATE.value, SensitivityLevel.SENSITIVE.value}
DAILY_EXTRACT_LIMIT = 24
DAILY_MAINTENANCE_LLM_LIMIT = 4
MICROBATCH_MAX_EVENTS = 8
MICROBATCH_WAIT = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class OperationEventResult:
    state: str
    memory_ids: tuple[str, ...] = ()
    handoff_id: str | None = None
    skipped: bool = False
    deferred_until: datetime | None = None


class MemoryOperationsCoordinator:
    """Single owner for low-cost ingress policy and autonomous maintenance."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @staticmethod
    def classify_event(event: RawEvent) -> str:
        """Return ``priority``, ``ordinary`` or ``noise`` without a model call."""
        text = " ".join((event.content or "").lower().split())
        metadata = dict(event.event_metadata or {})
        if metadata.get("runtime_handoff_response") or metadata.get("correction_of_event_id"):
            return "priority"
        if any(marker in text for marker in EXPLICIT_MARKERS):
            return "priority"
        if len(text) <= 3 or text in NOISE_MARKERS:
            return "noise"
        if event.source_type.value == "conversation" and metadata.get("signal_kind"):
            return "priority"
        return "ordinary"

    async def process_event(self, event: RawEvent) -> OperationEventResult:
        """Run the only live ingestion path after deterministic noise gating."""
        kind = self.classify_event(event)
        if kind == "noise":
            event.processing_result = "discarded_noise"
            event.retention_state = "purge_30d"
            event.purge_after = datetime.now(UTC) + timedelta(days=30)
            return OperationEventResult("DISCARDED", skipped=True)

        if kind == "ordinary":
            ready, deferred_until = await self._prepare_microbatch(event)
            if not ready:
                return OperationEventResult(
                    "DEFERRED", skipped=True, deferred_until=deferred_until
                )

        if await self._extract_budget_exhausted(event.user_id):
            return OperationEventResult(
                "DEFERRED", skipped=True, deferred_until=self._next_utc_day()
            )

        # Keep the runtime compatibility boundary here.  It is also the
        # fail-closed seam used by health tests and rollout switches.
        from src.execution.runtime.working_agent import run_working_active

        raw_event = {
                "id": event.id,
                "content": event.content,
                "user_id": event.user_id,
                "visibility_scope": event.visibility_scope,
                "project_id": event.project_id,
                "repo_id": event.repo_id,
                "workspace_id": event.workspace_id,
                "source_type": event.source_type,
                "sensitivity": event.sensitivity,
                "occurred_at": event.occurred_at,
                "metadata": event.event_metadata or {},
                "event_metadata": event.event_metadata or {},
            }
        if (event.event_metadata or {}).get("batch_source_event_ids"):
            from src.execution.runtime.working_coordinator import WorkingCoordinator
            active = await WorkingCoordinator(self.db).process(event.id)
        else:
            active = await run_working_active(self.db, raw_event=raw_event)
        if active is None:
            raise RuntimeError("working_agent_unavailable")
        event.retention_state = "active"
        return OperationEventResult(
            active.state.value,
            active.memory_ids,
            active.handoff_id,
        )

    async def _prepare_microbatch(self, event: RawEvent) -> tuple[bool, datetime | None]:
        """Hold ordinary ingress briefly, then process one source-grounded batch."""
        metadata = dict(event.event_metadata or {})
        existing = metadata.get("batch_source_event_ids")
        if isinstance(existing, list) and event.id in existing:
            return True, None

        now = datetime.now(UTC)
        queued = list(
            (
                await self.db.execute(
                    select(RawEvent)
                    .where(
                        RawEvent.user_id == event.user_id,
                        RawEvent.source_type == event.source_type,
                        RawEvent.sensitivity == event.sensitivity,
                        RawEvent.visibility_scope == event.visibility_scope,
                        RawEvent.processing_status.in_(
                            (ProcessingStatus.QUEUED, ProcessingStatus.PROCESSING)
                        ),
                    )
                    .order_by(RawEvent.occurred_at.asc())
                    .limit(MICROBATCH_MAX_EVENTS)
                )
            ).scalars()
        )
        ids = list(dict.fromkeys([event.id, *(item.id for item in queued)]))[:MICROBATCH_MAX_EVENTS]
        due_at = (event.occurred_at or now) + MICROBATCH_WAIT
        if len(ids) < MICROBATCH_MAX_EVENTS and now < due_at:
            return False, due_at

        metadata["batch_source_event_ids"] = ids
        metadata["batch_kind"] = "source_microbatch"
        event.event_metadata = metadata
        return True, None

    async def _extract_budget_exhausted(self, user_id: str) -> bool:
        day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        calls = await self.db.scalar(
            select(func.coalesce(func.sum(AgentRun.model_call_count), 0))
            .join(AgentSession, AgentSession.id == AgentRun.session_id)
            .where(
                AgentRun.user_id == user_id,
                AgentRun.started_at >= day_start,
                AgentSession.agent_role == AgentRole.WORKING,
            )
        )
        return int(calls or 0) >= DAILY_EXTRACT_LIMIT

    @staticmethod
    def _next_utc_day() -> datetime:
        now = datetime.now(UTC)
        return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    async def run_maintenance(
        self,
        *,
        kind: str,
        user_id: str | None = None,
        limit_per_user: int = 50,
    ) -> dict[str, int]:
        """Run a resumable maintenance pass; safe to call repeatedly."""
        now = datetime.now(UTC)
        bucket = now.strftime("%Y-%m-%d") if kind != "weekly" else now.strftime("%G-W%V")
        key = f"maintenance:v2.5:{kind}:{user_id or 'all'}:{bucket}"
        run = await self.db.scalar(
            select(MemoryMaintenanceRun).where(MemoryMaintenanceRun.idempotency_key == key)
        )
        if run is not None and run.state == "completed":
            return dict(run.counters or {})
        if run is None:
            run = MemoryMaintenanceRun(
                id=generate_id("mmr"),
                user_id=user_id,
                kind=kind,
                state="running",
                idempotency_key=key,
                cursor={},
                counters={},
                token_budget=DAILY_MAINTENANCE_LLM_LIMIT * 400,
                token_used=0,
            )
            self.db.add(run)
            await self.db.flush()

        counters = {"merged": 0, "sealed": 0, "purged": 0, "briefs": 0, "expired": 0, "held": 0}
        try:
            users = [user_id] if user_id else await self._maintenance_users()
            for owner in users:
                if not owner:
                    continue
                merge_stats = await self._merge_safe_duplicates(run, owner, limit_per_user)
                for name, value in merge_stats.items():
                    counters[name] = counters.get(name, 0) + value
                counters["briefs"] += int(await self.refresh_user_brief(owner))
                if kind in {"daily", "weekly", "retention"}:
                    retention = await self._apply_retention(run, owner, limit_per_user)
                    for name, value in retention.items():
                        counters[name] = counters.get(name, 0) + value
                counters["expired"] += await self._expire_conversation_artifacts(owner)
            run.counters = counters
            run.state = "completed"
            run.finished_at = datetime.now(UTC)
            await self.db.flush()
            return counters
        except Exception as exc:
            run.state = "failed"
            run.error = type(exc).__name__[:256]
            run.finished_at = datetime.now(UTC)
            await self.db.flush()
            raise

    async def refresh_user_brief(self, user_id: str) -> bool:
        """Build a bounded, deterministic context pack from formal memory only."""
        now = datetime.now(UTC)
        memories = list((await self.db.execute(
            select(CommittedMemory)
            .where(
                CommittedMemory.user_id == user_id,
                CommittedMemory.status == CommittedStatus.ACTIVE,
                or_(CommittedMemory.valid_until.is_(None), CommittedMemory.valid_until > now),
                CommittedMemory.sensitivity.in_((SensitivityLevel.PUBLIC, SensitivityLevel.NORMAL)),
            )
            .order_by(CommittedMemory.importance.desc(), CommittedMemory.updated_at.desc())
            .limit(20)
        )).scalars())
        ids = [item.id for item in memories]
        revision = hashlib.sha256("|".join(ids).encode("utf-8")).hexdigest()
        existing = await self.db.scalar(select(UserMemoryBrief).where(UserMemoryBrief.user_id == user_id))
        if existing is not None and existing.source_revision == revision:
            return False
        lines = ["# 当前正式记忆摘要"]
        for item in memories:
            body = " ".join((item.body or "").split())[:180]
            lines.append(f"- [{item.id}] {item.title[:100]}：{body}")
        content = "\n".join(lines) if len(lines) > 1 else "# 当前正式记忆摘要\n- 暂无可自动加载的正式记忆。"
        if existing is None:
            self.db.add(UserMemoryBrief(
                id=generate_id("umb"), user_id=user_id, content=content, memory_ids=ids,
                source_revision=revision, expires_at=now + timedelta(days=7),
                token_estimate=max(1, len(content) // 4),
            ))
        else:
            existing.content = content
            existing.memory_ids = ids
            existing.source_revision = revision
            existing.generated_at = now
            existing.expires_at = now + timedelta(days=7)
            existing.token_estimate = max(1, len(content) // 4)
        from src.execution.services.conversation_memory_projector import try_refresh_conversation_memory_projection
        await try_refresh_conversation_memory_projection(self.db, user_id=user_id)
        return True

    async def _maintenance_users(self) -> list[str]:
        return [
            row[0]
            for row in (await self.db.execute(
                select(CommittedMemory.user_id).where(CommittedMemory.user_id.is_not(None)).distinct()
            )).all()
            if row[0]
        ]

    async def _merge_safe_duplicates(self, run: MemoryMaintenanceRun, user_id: str, limit: int) -> dict[str, int]:
        stats = {"merged": 0, "held": 0}
        pairs = await MemoryDeduplicator(self.db).find_duplicates(
            user_id=user_id, similarity_threshold=0.90, top_k=min(limit, 50)
        )
        for pair in pairs:
            first = await self.db.get(CommittedMemory, pair["memory_id_a"])
            second = await self.db.get(CommittedMemory, pair["memory_id_b"])
            if not self._merge_allowed(first, second):
                continue
            similarity = float(pair["similarity"])
            action_key = self._action_key("merge", user_id, [first.id, second.id], str(similarity))
            if await self.db.scalar(select(MemoryMaintenanceAction.id).where(MemoryMaintenanceAction.idempotency_key == action_key)):
                continue
            if similarity < 0.96:
                confirmed = await self._confirm_near_duplicate(run, first, second)
                if not confirmed:
                    await self._record_action(run, user_id, "merge", "held", [first.id, second.id], [], "near_duplicate_not_confirmed", action_key)
                    stats["held"] += 1
                    continue
            primary, secondary = self._choose_merge_primary(first, second)
            await MemoryDeduplicator(self.db).merge(
                primary.id, secondary.id, regenerate_embedding=False, expected_user_id=user_id
            )
            self.db.add(MemoryRelation(
                id=generate_id("mrel"), user_id=user_id, source_memory_id=secondary.id,
                target_memory_id=primary.id, relation_type="duplicates",
                reason="working_agent_auto_merge_v2.5", confidence=similarity,
                valid_from=datetime.now(UTC),
            ))
            await self._record_action(
                run, user_id, "merge", "completed", [primary.id, secondary.id], [],
                "exact_duplicate" if similarity >= 0.96 else "model_confirmed_near_duplicate",
                action_key, output_memory_id=primary.id,
            )
            stats["merged"] += 1
        return stats

    @staticmethod
    def _merge_allowed(first: CommittedMemory | None, second: CommittedMemory | None) -> bool:
        if first is None or second is None or first.status != CommittedStatus.ACTIVE or second.status != CommittedStatus.ACTIVE:
            return False
        if first.memory_type != second.memory_type or first.sensitivity != second.sensitivity:
            return False
        if first.visibility_scope != second.visibility_scope:
            return False
        if first.sensitivity.value in SENSITIVE_VALUES:
            return False
        blocked = {"persona_hypothesis", "relationship"}
        return first.memory_type.value not in blocked and second.memory_type.value not in blocked

    @staticmethod
    def _choose_merge_primary(first: CommittedMemory, second: CommittedMemory) -> tuple[CommittedMemory, CommittedMemory]:
        first_score = (float(first.importance or 0), first.created_at or datetime.min.replace(tzinfo=UTC))
        second_score = (float(second.importance or 0), second.created_at or datetime.min.replace(tzinfo=UTC))
        return (first, second) if first_score >= second_score else (second, first)

    async def _confirm_near_duplicate(self, run: MemoryMaintenanceRun, first: CommittedMemory, second: CommittedMemory) -> bool:
        if int(run.token_used or 0) + 400 > int(run.token_budget or 0):
            return False
        from src.shared.llm.model_gateway import ModelGateway
        from src.shared.llm.providers import get_llm_provider
        prompt = (
            "只输出 JSON：{\"same_fact\":true|false,\"confidence\":0..1}。"
            "仅当两条用户正式记忆在条件、时间和含义上完全相同才 true；否则 false。\n"
            f"A: {first.title}\n{first.body[:1000]}\nB: {second.title}\n{second.body[:1000]}"
        )
        try:
            raw = await ModelGateway(get_llm_provider()).generate_text(
                prompt, temperature=0.0, max_tokens=120,
                prompt_id="working-maintenance-dedup", prompt_version="v2.5",
            )
            run.token_used = int(run.token_used or 0) + 400
            data = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
            return bool(data.get("same_fact")) and float(data.get("confidence") or 0) >= 0.90
        except Exception:
            return False

    async def _apply_retention(self, run: MemoryMaintenanceRun, user_id: str, limit: int) -> dict[str, int]:
        now = datetime.now(UTC)
        stats = {"sealed": 0, "purged": 0}
        active_source_events = list((await self.db.execute(
            select(RawEvent)
            .join(MemorySource, MemorySource.raw_event_id == RawEvent.id)
            .join(CommittedMemory, CommittedMemory.id == MemorySource.memory_id)
            .where(
                RawEvent.user_id == user_id,
                RawEvent.processing_status == ProcessingStatus.COMPLETED,
                RawEvent.occurred_at <= now - timedelta(days=180),
                RawEvent.sensitivity.notin_((SensitivityLevel.PRIVATE, SensitivityLevel.SENSITIVE)),
                CommittedMemory.status == CommittedStatus.ACTIVE,
            )
            .distinct()
            .limit(limit)
        )).scalars())
        for event in active_source_events:
            if await self._seal_and_delete(run, event):
                stats["sealed"] += 1

        disposable = list((await self.db.execute(
            select(RawEvent)
            .where(
                RawEvent.user_id == user_id,
                RawEvent.processing_status == ProcessingStatus.COMPLETED,
                RawEvent.sensitivity.notin_((SensitivityLevel.PRIVATE, SensitivityLevel.SENSITIVE)),
                RawEvent.occurred_at <= now - timedelta(days=30),
            )
            .order_by(RawEvent.occurred_at.asc()).limit(limit)
        )).scalars())
        for event in disposable:
            if await self._purge_if_disposable(run, event, now):
                stats["purged"] += 1
        return stats

    async def _seal_and_delete(self, run: MemoryMaintenanceRun, event: RawEvent) -> bool:
        existing = await self.db.scalar(select(EvidenceSeal).where(EvidenceSeal.user_id == event.user_id, EvidenceSeal.source_event_id == event.id))
        seal = existing or EvidenceSeal(
            id=generate_id("ese"), user_id=event.user_id, source_type=event.source_type.value,
            source_event_id=event.id, content_hash=event.content_hash, excerpt=event.content[:500],
            occurred_at=event.occurred_at, sensitivity=event.sensitivity.value,
            seal_metadata={"retention": "v2.5", "original_length": len(event.content or "")},
        )
        if existing is None:
            self.db.add(seal)
            await self.db.flush()
        key = self._action_key("compact", event.user_id, [], event.id)
        if await self.db.scalar(select(MemoryMaintenanceAction.id).where(MemoryMaintenanceAction.idempotency_key == key)):
            return False
        await self.db.execute(
            MemorySource.__table__.update().where(MemorySource.raw_event_id == event.id).values(raw_event_id=None, evidence_seal_id=seal.id)
        )
        await self.db.execute(
            MemoryWorkEvidence.__table__.update().where(MemoryWorkEvidence.raw_event_id == event.id).values(raw_event_id=None, evidence_seal_id=seal.id)
        )
        await self._record_action(run, event.user_id, "compact", "completed", [], [event.id], "active_memory_evidence_sealed", key, evidence_seal_id=seal.id)
        await self._queue_event_delete(event)
        await self.db.delete(event)
        return True

    async def _purge_if_disposable(self, run: MemoryMaintenanceRun, event: RawEvent, now: datetime) -> bool:
        if event.retention_state == "sealed":
            return False
        has_memory_source = await self.db.scalar(select(MemorySource.id).where(MemorySource.raw_event_id == event.id).limit(1))
        if has_memory_source:
            return False
        evidence_rows = list((await self.db.execute(
            select(MemoryWorkEvidence, MemoryWorkCase)
            .join(MemoryWorkCase, MemoryWorkCase.id == MemoryWorkEvidence.case_id)
            .where(MemoryWorkEvidence.raw_event_id == event.id)
        )).all())
        age = now - (event.occurred_at or now)
        if evidence_rows and (age < timedelta(days=90) or any(case.status not in {"discarded", "resolved"} for _, case in evidence_rows)):
            return False
        if await self.db.scalar(select(AgentHandoff.id).where(AgentHandoff.source_event_id == event.id, AgentHandoff.status == AgentHandoffStatus.ACTIVE).limit(1)):
            return False
        key = self._action_key("purge", event.user_id, [], event.id)
        if await self.db.scalar(select(MemoryMaintenanceAction.id).where(MemoryMaintenanceAction.idempotency_key == key)):
            return False
        if evidence_rows:
            await self.db.execute(MemoryWorkEvidence.__table__.delete().where(MemoryWorkEvidence.raw_event_id == event.id))
        await self._record_action(run, event.user_id, "purge", "completed", [], [event.id], "noise_30d" if not evidence_rows else "closed_case_90d", key)
        await self._queue_event_delete(event)
        await self.db.delete(event)
        return True

    async def _expire_conversation_artifacts(self, user_id: str) -> int:
        now = datetime.now(UTC)
        handoffs = await self.db.execute(
            AgentHandoff.__table__.update()
            .where(AgentHandoff.user_id == user_id, AgentHandoff.status == AgentHandoffStatus.ACTIVE, AgentHandoff.expires_at.is_not(None), AgentHandoff.expires_at <= now)
            .values(status=AgentHandoffStatus.EXPIRED)
        )
        candidates = await self.db.execute(
            ConversationAttentionCandidate.__table__.update()
            .where(ConversationAttentionCandidate.user_id == user_id, ConversationAttentionCandidate.status == "pending", ConversationAttentionCandidate.expires_at.is_not(None), ConversationAttentionCandidate.expires_at <= now)
            .values(status="expired")
        )
        return int(handoffs.rowcount or 0) + int(candidates.rowcount or 0)

    async def _record_action(
        self, run: MemoryMaintenanceRun, user_id: str, action: str, state: str,
        memory_ids: list[str], event_ids: list[str], reason: str, key: str,
        *, output_memory_id: str | None = None, evidence_seal_id: str | None = None,
    ) -> None:
        self.db.add(MemoryMaintenanceAction(
            id=generate_id("mma"), run_id=run.id, user_id=user_id, action=action, state=state,
            input_memory_ids=memory_ids, input_event_ids=event_ids, output_memory_id=output_memory_id,
            evidence_seal_id=evidence_seal_id, reason_code=reason, details={}, idempotency_key=key,
            reversible_until=datetime.now(UTC) + timedelta(days=30) if action in {"merge", "supersede"} else None,
        ))

    @staticmethod
    def _action_key(action: str, user_id: str, memory_ids: Iterable[str], suffix: str) -> str:
        basis = f"v2.5:{action}:{user_id}:{'|'.join(sorted(memory_ids))}:{suffix}"
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()

    async def _queue_event_delete(self, event: RawEvent) -> None:
        try:
            from src.memory.services.graph_projection import queue_source_deletion
            await queue_source_deletion(
                self.db, user_id=event.user_id, project_id=event.project_id,
                source_kind="raw_event", source_id=event.id,
                source_revision=event.content_hash or event.id,
            )
        except Exception:
            # Compaction must not fail only because a derived graph is offline;
            # the graph outbox has its own recovery loop.
            return

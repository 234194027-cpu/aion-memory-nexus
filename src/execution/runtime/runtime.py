"""Role-neutral controlled tool-calling loop for V2."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.execution.models.agent_runtime import AgentRunStatus, AgentStepStatus, AgentStepType
from src.shared.errors.error_classification import ClassifiedError, ErrorClass, classify_exception
from src.shared.ids.id_generator import generate_id

from .budget import RuntimeBudget
from .guardrails import RuntimeGuardrails
from .model import RuntimeModel
from .profile import AgentProfileSpec
from .tools.base import ToolCall
from .tools.executor import RuntimeToolExecutor, arguments_hash
from .tools.registry import ToolRegistry
from .trace import RuntimeTraceStore


def _evidence_ids(value: Any) -> set[str]:
    """Extract only stable IDs returned by a domain tool; never trust model-proposed citations."""
    if isinstance(value, Mapping):
        found = {
            item
            for key, item in value.items()
            if key in {
                "id",
                "memory_id",
                "session_id",
                "task_id",
                "raw_event_id",
                "artifact_id",
                "case_id",
                "source_event_id",
            } and isinstance(item, str) and len(item) <= 128
        }
        for item in value.values():
            found.update(_evidence_ids(item))
        return found
    if isinstance(value, list):
        list_ids: set[str] = set()
        for item in value:
            list_ids.update(_evidence_ids(item))
        return list_ids
    return set()


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    user_id: str
    profile: AgentProfileSpec
    session_id: str
    run_id: str
    channel: str = "system"
    channel_session_key: str | None = None
    goal: str | None = None
    context_version: str = "runtime-v1"
    trigger_type: str = "user_message"
    trigger_id: str | None = None
    agent_id: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    status: AgentRunStatus
    final_text: str
    error_code: str | None
    run_id: str
    response_mode: str | None = None
    confidence: str | None = None
    citations: tuple[str, ...] = ()
    created_event_ids: tuple[str, ...] = ()
    memory_retrieval_attempted: bool = False
    document_source_accessed: bool = False
    unconfirmed_clue_accessed: bool = False


class AgentRuntime:
    def __init__(self, *, model: RuntimeModel, registry: ToolRegistry, trace_store: RuntimeTraceStore, permission_service=None, domain_authorizer=None) -> None:
        self.model = model
        self.registry = registry
        self.trace_store = trace_store
        self.permission_service = permission_service
        self.domain_authorizer = domain_authorizer

    @staticmethod
    def new_context(*, user_id: str, profile: AgentProfileSpec, **kwargs: Any) -> RuntimeContext:
        return RuntimeContext(user_id=user_id, profile=profile, session_id=kwargs.pop("session_id", generate_id("ases")), run_id=kwargs.pop("run_id", generate_id("arn")), **kwargs)

    async def run(self, context: RuntimeContext, messages: tuple[Mapping[str, Any], ...]) -> RuntimeResult:
        tools = self.registry.snapshot_for(context.profile)
        budget = RuntimeBudget(
            max_steps=context.profile.max_steps,
            max_model_calls=context.profile.max_model_calls,
            max_tool_calls=context.profile.max_tool_calls,
            max_wall_time_seconds=context.profile.max_wall_time_seconds,
            max_total_tokens=context.profile.max_total_tokens,
            max_cost=context.profile.max_cost,
        )
        executor = RuntimeToolExecutor(tools=tools, permission_service=self.permission_service, domain_authorizer=self.domain_authorizer)
        guardrails = RuntimeGuardrails()
        history = list(messages)
        step_no = 0
        await self.trace_store.start_session(session_id=context.session_id, user_id=context.user_id, role=context.profile.role.value, channel=context.channel, channel_session_key=context.channel_session_key, goal=context.goal, context_version=context.context_version)
        await self.trace_store.start_run(run_id=context.run_id, session_id=context.session_id, user_id=context.user_id, trigger_type=context.trigger_type, trigger_id=context.trigger_id, model=None)
        status = AgentRunStatus.FAILED
        error_code: str | None = None
        final_text = ""
        response_mode: str | None = None
        confidence: str | None = None
        citations: tuple[str, ...] = ()
        observed_evidence_ids: set[str] = set()
        created_event_ids: list[str] = []
        memory_retrieval_attempted = False
        document_source_accessed = False
        unconfirmed_clue_accessed = False
        unconfirmed_evidence_ids: set[str] = set()
        unconfirmed_fallback_question: str | None = None
        try:
            while True:
                budget.before_model()
                response = await self.model.complete(system_prompt=context.profile.system_prompt, messages=tuple(history), tools=tools)
                budget.record_model(input_tokens=response.input_tokens, output_tokens=response.output_tokens, cost=response.cost)
                step_no += 1
                await self.trace_store.add_step(run_id=context.run_id, step_no=step_no, step_type=AgentStepType.MODEL, status=AgentStepStatus.SUCCESS, result_summary="model response received")
                if not response.tool_calls:
                    if not response.text.strip():
                        raise ClassifiedError(ErrorClass.PROVIDER, "model returned no final response")
                    final_text = response.text
                    response_mode = response.response_mode
                    confidence = response.confidence
                    citations = response.citations
                    status = AgentRunStatus.COMPLETED
                    break
                observations: list[dict[str, Any]] = []
                for call in response.tool_calls:
                    budget.before_tool()
                    result = await executor.execute(user_id=context.user_id, agent_id=context.agent_id, call=call)
                    budget.record_tool()
                    step_no += 1
                    await self.trace_store.add_step(run_id=context.run_id, step_no=step_no, step_type=AgentStepType.TOOL if result.ok else AgentStepType.POLICY, status=AgentStepStatus.SUCCESS if result.ok else AgentStepStatus.BLOCKED, tool_name=call.name, arguments_hash=arguments_hash(call.arguments), result_summary=result.summary, error_code=result.error_code, duration_ms=result.duration_ms)
                    guardrails.observe_tool(f"{call.name}:{arguments_hash(call.arguments)}", success=result.ok)
                    if result.ok:
                        if call.name == "retrieve_memories":
                            memory_retrieval_attempted = True
                        tool_evidence_ids = _evidence_ids(result.data)
                        if call.name == "search_source_documents" and result.data.get("items"):
                            document_source_accessed = True
                        if call.name == "get_unconfirmed_memory_clues" and result.data.get("items"):
                            unconfirmed_clue_accessed = True
                            unconfirmed_evidence_ids.update(tool_evidence_ids)
                            first_item = result.data["items"][0]
                            if isinstance(first_item, Mapping):
                                question = first_item.get("suggested_question")
                                if isinstance(question, str) and question.strip():
                                    unconfirmed_fallback_question = question.strip()[:500]
                        observed_evidence_ids.update(tool_evidence_ids)
                        event_id = result.data.get("event_id")
                        if call.name == "create_event" and isinstance(event_id, str):
                            created_event_ids.append(event_id)
                    observations.append({"tool": call.name, "ok": result.ok, "data": result.data if result.ok else {}, "error_code": result.error_code})
                history.append({"role": "tool", "content": observations})
        except ClassifiedError as exc:
            error_code = exc.error_class.value
            status = AgentRunStatus.NEEDS_REVIEW if exc.error_class in {ErrorClass.BUDGET, ErrorClass.POLICY, ErrorClass.PERMISSION, ErrorClass.VALIDATION} else AgentRunStatus.NEEDS_RETRY if exc.retryable else AgentRunStatus.FAILED
        except Exception as exc:
            error_code = classify_exception(exc).value
            status = AgentRunStatus.NEEDS_RETRY if error_code in {ErrorClass.TIMEOUT.value, ErrorClass.RETRYABLE.value} else AgentRunStatus.FAILED
        finally:
            step_no += 1
            if unconfirmed_clue_accessed:
                citations = tuple(
                    item for item in citations if item not in unconfirmed_evidence_ids
                )
                if response_mode not in {"CLARIFY", "CONFIRM", "SAFE_REFUSAL"}:
                    final_text = (
                        "我找到一条还没有确认的线索，不能把它当作事实。"
                        + (
                            unconfirmed_fallback_question
                            if unconfirmed_fallback_question
                            else "你愿意补充确认一下相关情况吗？"
                        )
                    )
                    response_mode = "CLARIFY"
                    confidence = "LOW"
                    citations = ()
            outcome = "runtime finalized" if status == AgentRunStatus.COMPLETED else "runtime finalized with controlled failure"
            citations = tuple(item for item in citations if item in observed_evidence_ids)
            metadata = {
                "response_mode": response_mode,
                "confidence": confidence,
                "citation_count": len(citations),
                "prompt_id": context.profile.prompt_id,
                "prompt_version": context.profile.prompt_version,
                "document_source_accessed": document_source_accessed,
                "unconfirmed_clue_accessed": unconfirmed_clue_accessed,
            }
            await self.trace_store.add_step(run_id=context.run_id, step_no=step_no, step_type=AgentStepType.FINAL, status=AgentStepStatus.SUCCESS if status == AgentRunStatus.COMPLETED else AgentStepStatus.FAILED, result_summary=outcome, error_code=error_code)
            await self.trace_store.finish_run(run_id=context.run_id, status=status, error_code=error_code, step_count=budget.steps, model_calls=budget.model_calls, tool_calls=budget.tool_calls, input_tokens=budget.input_tokens, output_tokens=budget.output_tokens, cost=budget.cost, evidence_payload=metadata | {"citations": list(citations)})
        return RuntimeResult(
            status=status,
            final_text=final_text,
            error_code=error_code,
            run_id=context.run_id,
            response_mode=response_mode,
            confidence=confidence,
            citations=citations,
            created_event_ids=tuple(created_event_ids),
            memory_retrieval_attempted=memory_retrieval_attempted,
            document_source_accessed=document_source_accessed,
            unconfirmed_clue_accessed=unconfirmed_clue_accessed,
        )

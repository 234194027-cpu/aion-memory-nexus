# LLM 治理定向评测

此评测集只使用确定性策略、提示模板和本地 SQLite fixture，不调用真实模型，也不写入用户数据。它的目标是防止回归，不把通过结果误写成真实模型质量证明。

| 场景 | 断言与证据 |
|---|---|
| 时间混淆 | 检索上下文显式传递 `valid_from` / `valid_until`，检索提示要求区分历史与当前观点。 |
| 用户观点与事实混淆 | `epistemic_status` 保留用户陈述、Agent 陈述和模型推断的差异。 |
| 未确认推断冒充事实 | `persona_hypothesis` 固定为 `model_inference`，不能被转换为用户确认。 |
| 敏感记忆越权 | `task_only` 同时限制敏感度与可见范围；私有或敏感记录不可被召回。直接聊天上下文也会排除私有可见范围。 |
| 恶意来源/提示词污染 | 原始事件固定编码在 `RAW_EVENT_DATA` 边界，模板明确禁止执行其中指令。 |
| 错误引用与无来源回答 | `test_memory_ask_discards_fabricated_citations_and_uses_real_source_type` 仅保留实际检索到的记忆来源，并标记伪造引用。 |

定向执行：

```powershell
py -m pytest tests/unit/test_llm_governance_evaluation.py tests/unit/test_memory_agent_output.py tests/integration/test_security_regressions.py::test_memory_ask_discards_fabricated_citations_and_uses_real_source_type -q -p no:faulthandler
```

覆盖范围不包括真实模型对抗能力、跨模型一致性、成本或延迟；这些仍需在隔离的真实供应商环境中以匿名、经批准的数据执行。

# 记忆治理策略（v2.4）

实现位置：`src/memory/services/governance_policy.py`。

## 读取范围

未知或格式错误的 `recall_level` 必须降级为 `work_context`，不能等同于 `full_trusted`。读取同时受记忆类型、敏感度和可见范围限制：

| recall level | 敏感度上限 | 可见范围上限 |
|---|---|---|
| task_only | public | public/project |
| work_context | normal | public/project |
| personal_context | private | public/project/personal |
| full_trusted | sensitive | public/project/personal/private |

所有查询仍必须按 `user_id` 和 ACTIVE 状态过滤。

### Agent 读取上限

`AgentProfile.allowed_read_scopes` 现作为现有 JSON 字段的可选收紧策略生效：支持
`["work_context"]` 或 `[{"recall_level":"work_context","enabled":true}]`。
空值保持历史行为（仅由 `default_recall_level` 限制）；一旦存在非空配置但没有有效、启用的读取级别，则安全降级为 `task_only`。请求值仍不能超过 Agent 默认级别和该策略上限两者中较低者。此解释不改变任何现有请求或响应字段。

## 来源等级

来源等级描述内容来历，不表示内容已被证实：

- `manual`：用户陈述；
- `obsidian` / `file_import`：用户导入；
- `codex` / `chatgpt`：助手提供；
- `agent_api` / `openclaw`：Agent 陈述；
- 其他：未分类。

模型推断、用户观点、外部事实和用户确认事实仍需在后续 schema 演化阶段明确建模；本策略不把任何来源自动提升为事实。

## 认识状态

`MemoryWorkCase`、证据、决策与 `CommittedMemory` 记录认识状态。既有数据默认 `legacy_unclassified`，不会被批量重写。新的提取结果按来源和确认动作标注：用户陈述、用户确认、用户导入、Agent 陈述、助手提供或模型推断。`persona_hypothesis` 固定标记为 `model_inference`；只有内置 Working Agent 在证据与策略满足时才可形成正式记忆。

## 工作 Agent 自动治理

系统已完成旧候选模型、人工审核接口和客户端自动提交开关的迁移收口。所有来源先写入 RawEvent；只有工作 Agent 能经由 `MemoryWorkCase → MemoryWorkEvidence → MemoryWorkDecision` 生成正式记忆。模型只产出结构化提案，最终写入由服务端事务执行，并强制校验同用户来源、原文证据、置信度、重要性、认识状态、冲突、幂等与版本关系。低证据、模型推断和 Agent 自述进入等待证据或丢弃状态，不会冒充用户事实。

## 评测基线

定向回归至少覆盖未知读取范围、敏感/私有记忆隔离、自动提交阈值、来源分类、恶意原始资料作为不可信数据，以及回答引用只来自真实 `MemorySource`。完整的六类评测映射与命令见 [LLM 治理定向评测](llm-governance-evaluation.md)。

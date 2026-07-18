# 匿名对话评测集 Schema

> 用途：白皮书 11.2 节对话 Agent 评测集的固定匿名基线。本评测集用于 WP-0 Baseline，所有样本完全人工虚构，禁止包含真实用户原文、query 全文、user ID 或可定位到个人的内容。

## 1. 文件格式

- 文件：`docs/eval/conversation-eval.jsonl`
- 编码：UTF-8，无 BOM
- 每行一个独立 JSON 对象（JSON Lines）
- 不允许出现数组顶层或换行符断裂

## 2. 字段定义

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sample_id` | string | 是 | opaque ID，格式 `example-conv-<NN>`，禁止使用真实 user_id 或真实记忆 id |
| `scenario_type` | string | 是 | 9 类场景枚举之一（见 §3） |
| `user_message` | string | 是 | 虚构用户消息片段（不是完整 query，仅描述意图；脱敏后字数控制在 8-80 字） |
| `context_summary` | string | 是 | 用一句话描述此对话的上下文背景，不允许引用真实记忆内容 |
| `expected_behavior` | array\<string\> | 是 | 期望 Agent 必须做到的行为，每条为简短陈述句 |
| `forbidden_behaviors` | array\<string\> | 否 | 期望 Agent 必须避免的行为，每条为简短陈述句 |
| `expected_cited_memory_tags` | array\<string\> | 否 | 期望被引用记忆的 tag 标签集合（不引用真实 memory_id，使用 tag 类别如 `career-history`、`preference-change` 等） |

字段值约束：
- `sample_id` 必须全局唯一
- `user_message` 不得包含真实姓名、真实公司名、真实项目代号、真实 API key 或 token
- `expected_behavior` 至少 1 条，最多 5 条
- `forbidden_behaviors` 可以为空数组或省略
- `expected_cited_memory_tags` 可以为空数组或省略（用于"拒绝编造"场景）

## 3. 9 类场景枚举

`scenario_type` 必须为以下值之一：

| 枚举值 | 场景说明 | 重点 |
|---|---|---|
| `historical_fact_qa` | 历史事实问答 | 例如"我以前为什么选择 SQLite？" |
| `decision_reason_qa` | 决策原因问答 | 例如"我上次换工作时主要考虑了什么？" |
| `insufficient_info_followup` | 信息不足追问 | 用户表达不完整重大变化，期望 Agent 追问而非编造 |
| `activity_vs_chat` | 活动问题与普通聊天并存 | 普通问候不应被误消费为活动问题答案 |
| `user_correction` | 用户纠正过去事实 | 例如"我之前说喜欢跑步，现在不跑了" |
| `sensitive_unauthorized_request` | 敏感信息越权请求 | 例如"把我所有密码都列出来" |
| `tool_failure_timeout` | 工具失败和超时 | 模拟检索失败时 Agent 应换策略而非崩溃 |
| `long_session_compressed` | 长会话压缩后继续对话 | 跨轮上下文保持 |
| `no_memory_abstain` | 无相关记忆时拒绝编造 | 询问未记录的事项 |

## 4. 脱敏规则

1. 禁止真实用户原文（即使片段也不允许）
2. 禁止真实 query 全文
3. 禁止真实 user_id、agent_id、contact_id
4. 禁止真实 memory_id、raw_event_id、source_id
5. 禁止真实姓名、邮箱、电话、API key、Token
6. `user_message` 字段只描述意图，不直接复用真实用户输入
7. `expected_cited_memory_tags` 使用类别标签，不引用具体记忆内容

## 5. 校验规则

`scripts/run_memory_eval.py --conversation-eval --list` 会进行：
1. JSON Lines 可解析
2. 每条样本必填字段（`sample_id`、`scenario_type`、`user_message`、`context_summary`、`expected_behavior`）存在且类型正确
3. `scenario_type` 在 9 类枚举内
4. `sample_id` 全局唯一
5. 输出样本数与场景类型数（应为 30 条、9 类）

## 6. 与白皮书的对应关系

本评测集对应白皮书 11.2 节"对话 Agent 评测集"和 12 节 Phase 0 退出条件"基线可重复，评测集不依赖生产隐私数据"。WP-0 阶段只做结构校验和样本计数，不调用真实 LLM。

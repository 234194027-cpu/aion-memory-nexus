# 匿名 RawEvent 评测集 Schema

> 用途：白皮书 11.3 节工作 Agent 评测集的固定匿名基线，覆盖 10 类抽取/治理/迁移对比场景。所有样本完全人工虚构，禁止包含真实 user_id、project_id、repo_id 或可定位到个人的原始事件内容。

## 1. 文件格式

- 文件：`docs/eval/raw-event-eval.jsonl`
- 编码：UTF-8，无 BOM
- 每行一个独立 JSON 对象（JSON Lines）

## 2. 字段定义

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sample_id` | string | 是 | opaque ID，格式 `example-raw-<NN>`，禁止使用真实 raw_event_id |
| `event_kind` | string | 是 | 10 类事件枚举之一（见 §3） |
| `anonymized_content` | string | 是 | 脱敏后的事件内容描述（不是真实原文；只描述意图和结构） |
| `event_metadata` | object | 是 | 元数据 JSON 对象；禁止包含真实 user_id/project_id/repo_id，使用 `example-*` 占位 |
| `expected_candidates` | object | 是 | 期望抽取结果，含字段：`count`、`types`、`importance_range`、`confidence_range`、`suggested_action` |
| `expected_dup_against` | string | 否 | 期望与之去重对比的 opaque ID（用于同义重复/重试幂等场景） |

字段值约束：
- `sample_id` 全局唯一
- `anonymized_content` 长度 8-200 字
- `event_metadata` 必须是 JSON 对象，可包含 `fictitious_source`（虚构来源标记）、`fictitious_timestamp`（ISO8601）
- `expected_candidates.count` 必须为非负整数
- `expected_candidates.types` 为字符串数组，元素来自 `MemoryType` 枚举（decision/preference/fact/insight/task/project_context/principle/correction/timeline_event/persona_hypothesis）
- `expected_candidates.importance_range` 形如 `{"min": 0.0, "max": 1.0}`
- `expected_candidates.confidence_range` 形如 `{"min": 0.0, "max": 1.0}`
- `expected_candidates.suggested_action` 为 `accept`/`reject`/`defer`/`needs_more_evidence`/`merge` 之一

## 3. 10 类事件枚举

`event_kind` 必须为以下值之一：

| 枚举值 | 场景说明 | 重点 |
|---|---|---|
| `single_fact` | 单事实事件 | 抽取 1 个候选 |
| `multi_fact` | 多事实事件 | 抽取多个候选 |
| `noise` | 噪音事件 | 不应抽取任何候选 |
| `synonym_duplicate` | 同义重复 | 与已有记忆去重，标记 duplicate |
| `preference_change` | 偏好变化 | 创建新候选并标记 conflict |
| `explicit_correction` | 明确纠正 | 标记旧记忆为 superseded |
| `temporal_validity` | 时间有效性 | 设置 valid_from / valid_until |
| `source_credibility_diff` | 来源可信度差异 | 不同来源相同信息，置信度不同 |
| `low_confidence_inference` | 低置信度推断 | 标记为 needs_more_evidence |
| `retry_idempotent` | 重试幂等 | 重复处理不应产生重复候选 |

## 4. 脱敏规则

1. `anonymized_content` 禁止包含真实用户原文（即使片段）
2. `event_metadata` 禁止包含真实 user_id、project_id、repo_id、workspace_id
3. 禁止包含真实 source_id、agent_id、contact_id
4. 禁止包含真实姓名、邮箱、电话、API key、Token
5. 所有 ID 字段使用 `example-*` 前缀作为 opaque 标识

## 5. 校验规则

`scripts/run_memory_eval.py --raw-event-eval --list` 会进行：
1. JSON Lines 可解析
2. 每条样本必填字段（`sample_id`、`event_kind`、`anonymized_content`、`event_metadata`、`expected_candidates`）存在且类型正确
3. `event_kind` 在 10 类枚举内
4. `sample_id` 全局唯一
5. 输出样本数与事件种类数（应为 40 条、10 类）

## 6. 与白皮书的对应关系

本评测集对应白皮书 11.3 节"工作 Agent 评测集"和 7.6 节"迁移对比"需求。WP-0 阶段只做结构校验和样本计数，不调用真实 LLM。

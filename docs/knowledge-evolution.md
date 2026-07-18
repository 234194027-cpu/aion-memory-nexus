# Wiki 知识演化与来源透明度

## 当前模型

- `knowledge_pages`：当前自动聚合主题；
- `knowledge_page_memories`：主题与 ACTIVE 记忆的关联，`relation_basis` 说明关联依据；
- `knowledge_page_versions`：仅保存聚合后的标题、摘要、统计值、关联记忆 ID、变更原因与时间，不复制原始正文；
- `memory_relations`：关系类型、理由、置信度、创建时间及可选有效期。

## Wiki 变更原因

| 值 | 含义 |
|---|---|
| `initial_aggregation` | 首次形成主题或为历史页面建立基线快照 |
| `membership_changed` | 标签关联的 ACTIVE 记忆集合发生变化 |
| `derived_summary_changed` | 聚合摘要或置信度发生变化 |
| `no_active_members` | 主题没有可用 ACTIVE 记忆，当前页面被移除 |

版本不是用户原文的替代品，原始证据仍通过 `MemorySource → RawEvent` 回查。

## 删除与过期

- hard delete/tombstone：删除包含该记忆 ID 的 Wiki 版本，避免删除后仍从历史派生层暴露信息；
- revoke/expire/supersede：保留历史版本，但当前 Wiki 仅按 ACTIVE 记忆重建；
- 关系边在 delete 时删除；在 revoke/expire 时保留关系历史但当前图谱过滤非 ACTIVE 端点。

## 置信度与不确定性

页面和关联记忆返回 `confidence_state`：低于 0.5 为 `low`，0.5–0.749 为 `review`，其余为 `supported`。这只是证据/提取置信度提示，不等价于外部事实验证。

## 待确认

- 用户可编辑 Wiki 正文、版本回滚和冲突解决流程；
- 外部资料的独立事实校验与可信来源审批；
- 关系有效期的自动推断规则。当前仅接受显式 API 输入。

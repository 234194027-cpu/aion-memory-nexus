# 数据生命周期与恢复 Runbook

## 当前可用能力

- `GET /api/data-portability/export`：当前登录用户的只读 JSON 导出。
- `POST /api/memory/{memory_id}/forget`：revoke、expire、delete、supersede。
- `DELETE /api/events/{event_id}`：删除原始事件；无其他来源的关联记忆会 tombstone。
- `data_lifecycle_audits`：不含正文的生命周期审计记录。

## 导出边界

导出格式为 `life-memory-export/v3`。它包含用户拥有的原始事件、记忆案件、证据、治理决策、正式记忆、来源、关系、Wiki 派生数据、媒体元数据、Obsidian 同步元数据和生命周期审计。

它不会包含：密码哈希、Token、任何供应商/企微配置与密钥、embedding 向量和内容快照、媒体二进制、机器本地路径、远程来源 URL、企微媒体 ID。导入后应按当前环境重新配置供应商和重新构建向量索引。

## 删除语义

| 操作 | 原始事件 | 正式记忆 | 来源/向量 | Wiki/关系 | 审计 |
|---|---|---|---|---|---|
| revoke / expire | 保留 | 状态改为非 ACTIVE | 保留 | Wiki 按 ACTIVE 集合重建；关系历史保留 | 记录 |
| supersede | 保留 | 旧记忆标记 SUPERSEDED，新记忆继承来源 | 来源复制到新记忆 | Wiki 按 ACTIVE 集合重建 | 记录 |
| delete | 保留 | tombstone，ID 保留 | 删除来源、embedding；外部索引 best effort 删除 | 删除关联边；Wiki 重建 | 记录 |
| 删除 RawEvent | 删除 | 仅有该来源的记忆 tombstone | 删除对应来源；需要 tombstone 时同上 | 同上 | 记录事件和受影响记忆 |

## 恢复预演（人工执行）

自动恢复 API 和页面尚未实施。任何恢复前必须：

1. 在隔离数据库验证 export manifest 的格式、集合数量和用户 ID；
2. 核验导出文件来源、加密存放位置和审计授权；
3. 按 `RawEvent → MemoryWorkCase/Evidence/Decision → CommittedMemory → MemorySource/Relation → Wiki` 顺序导入；
4. 不导入 embedding；在目标环境重新生成向量索引与 Wiki；
5. 核对 tombstone/REVOKED/EXPIRED/SUPERSEDED 状态不会被重新激活；
6. 经人工确认后才允许切换到恢复数据库。

## 上线前待确认

- 导出文件的加密、保存位置、下载审计与有效期；
- 备份 RPO/RTO、备份副本保留期，以及“忘记”对历史备份的处理政策；
- 多账户恢复时的 ID 冲突、覆盖、合并和授权模型；
- 生产 PostgreSQL/Redis 的备份恢复演练。

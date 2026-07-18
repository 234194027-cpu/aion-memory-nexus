# 可信记忆中枢 V3：Graphiti 投影运行手册

## 边界

PostgreSQL/pgvector 中的 `RawEvent → MemoryWorkCase / Evidence / Decision → CommittedMemory`
是唯一权威链路。Neo4j 和 Graphiti 只保存可删除、可重建的时间关系投影：它们不能通过
HTTP、MCP 或外部 Agent 直接写入正式记忆，也不能反向触发正式记忆提交。

外部 Agent（含 Codex、OpenClaw、MCP 和对话 Agent）只能调用事件入口。只有内置
Working Agent 可经 `MemoryCommitService` 形成正式记忆；低置信、冲突、敏感或身份推断
保留在工作案件中等待证据。

## 启用顺序

1. 保持 `GRAPHITI_ENABLED=false` 部署本地验证过的源代码和迁移 `026`。
2. 在服务器环境文件中设置 `GRAPHITI_NEO4J_PASSWORD`、Neo4j URI、与现有云模型兼容的
   `GRAPHITI_EMBEDDING_MODEL`，以及多用户环境的 `GRAPHITI_ADMIN_USER_IDS`；密码不得进入
   仓库、日志或管理接口。`GRAPHITI_ADMIN_USER_IDS` 为空时非单用户环境会拒绝图谱运维操作。
3. 以 `docker compose --profile graphiti up -d neo4j` 启动仅内部网络可达的 Neo4j。
4. 先保持 `GRAPHITI_SHADOW_MODE=true`，调用 `POST /api/graph/replay` 的 `dry_run=true`，
   再按小批次回放。重跑按 source/revision/operation 幂等，不产生重复 Episode。
5. 用真实 DeepSeek 兼容接口验证结构化抽取、限流、投影失败重试和源数量一致性后，才可灰度
   设置 `GRAPHITI_ENABLED=true`。图谱不可用时，权威写入与既有 pgvector/BM25 检索必须继续工作。

## 数据流与敏感内容

投影任务表仅保存对象 ID、版本、范围、生命周期和错误码；Worker 在处理时才从权威库读取原文。
因此私密内容若被允许进入现有云模型边界，会按已配置的模型通道传输以抽取关系。不得在指标、
错误信息、日志或管理页面记录正文。图谱结果在接入检索前必须再次验证用户、项目、可见范围、
来源状态和有效期；无来源、越权、撤回或过期关系必须丢弃。

## 运维检查

- `GET /api/graph/status`：只返回开关与任务计数。
- `GET /api/graph/failures`：返回失败任务的 ID 和错误类型，不返回内容。
- `POST /api/graph/failures/{projection_id}/retry`：重新入队可恢复失败。

以上图谱运维接口仅允许单用户模式的唯一 owner，或 `GRAPHITI_ADMIN_USER_IDS` 显式列出的
多用户管理员调用；未配置时多用户环境失败关闭。
- 删除/撤回正式记忆和删除事件都会写 Delete 投影任务；若当前 Graphiti 版本不支持删除 API，
  任务会明确失败并保留在队列中，不能假报完成。完成 Graphiti 版本兼容验证后再允许生产回放。

当前初始本体限定为人、项目、仓库、任务、决策、偏好、事件，以及关联、发生于、负责、依赖、
支持、矛盾、修正、替代八类关系。任何扩展都必须先修改治理代码、迁移和验收测试。
当前 Docker 组合使用 Neo4j Community；Graphiti 0.29 把 Neo4j `group_id` 映射到数据库名，
不能把用户 ID 假装成隔离分组。因此首发默认要求 `SOLO_MODE=true`；非单用户环境会让图谱
Worker 失败关闭，继续使用既有检索，直到每租户独立 Neo4j 数据库/实例的隔离方案通过验收。

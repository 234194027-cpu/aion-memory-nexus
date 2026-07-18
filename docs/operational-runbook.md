# 运行可观测性与失败处理 Runbook

## 最小观测面

- `/health`：数据库、Redis 连通性、LLM 配置、调度器、企微连接状态、向量存储能力和 Alembic 版本记录；数据库不可用时为 `degraded`。企微检查不会建立连接或发消息；迁移字段仅表示版本表可读，不替代部署前 `alembic upgrade head` 校验。
- `/metrics`：进程内 HTTP、LLM/embedding、企微 token/连接/发送、向量检索/索引和后台任务计数。生产环境访问需要系统 Token。
- `X-Request-ID`：每个 HTTP/WebSocket 请求均生成或透传安全格式关联 ID，并回写响应头。仅允许 1–128 位字母、数字、`.`、`_`、`-`；其他值会被替换。

指标只记录固定操作名、状态和耗时，不记录用户正文、提示词、来源 quote、向量、Token、模型原始响应或异常原文。

## 失败边界

| 场景 | 当前策略 | 人工处理入口 |
|---|---|---|
| 记忆提取失败 | RawEvent 状态设为 `FAILED`；日志仅含事件 ID 与异常类型 | 检查事件状态后重新触发提取或人工审核 |
| embedding 失败 | 指数退避重试；最终失败仅记录任务计数/异常类型 | 重跑 embedding backfill；数据库记忆仍可关键词检索 |
| 外部向量索引失败 | best effort；数据库为权威源 | 重新构建索引，不将索引状态当作数据丢失 |
| LLM/Embedding 不可用 | provider fallback 或调用失败指标增加 | 配置供应商、检查网络/限额，然后重试业务操作 |
| 企微未连接或发送失败 | health 显示 `disconnected`；固定 `wecom_connection` / `wecom_send` 指标增加 | 检查 bot 配置、企微网络和连接状态后通过既有连接接口恢复 |
| 向量检索/索引降级 | 记录 `vector_search` / `vector_index_create`；数据库仍是权威源 | 检查 pgvector 能力和索引后重建，不将索引状态当作数据丢失 |
| Redis 不可用 | health 显示 disconnected；数据库核心路径不依赖 Redis | 检查 Redis ACL/密码/网络并恢复服务 |

当前没有引入新的死信队列基础设施；`RawEvent.processing_status=FAILED` 是最小人工处理队列。生产如需要自动重放、跨进程可见队列或告警分派，需先确认 Celery/Redis 持久化、重试次数和人工值班策略。

## 生产上线待确认

先在目标主机、迁移和重启前执行只读预检；脚本不会打印环境变量值、密码、Token 或连接 URL：

```bash
python3 scripts/production_preflight.py --env-file .env.production --compose-file docker-compose.yml --cert-dir certs
```

预检会阻止占位密钥、弱 CORS、开发认证开关、缺失证书、Redis 无认证和浮动镜像标签；它不能替代真实的备份恢复演练，会明确输出 `MANUAL`。

- Redis ACL、密码、TLS 和持久化；
- PostgreSQL、Redis、Nginx 镜像 digest 与补丁策略；
- 正式证书、CORS 域名、密钥轮换和审计日志保留；
- 备份 RPO/RTO、恢复演练与“忘记”在备份副本中的政策；
- LLM 429、超时、断流、费用上限和数据出站审批。

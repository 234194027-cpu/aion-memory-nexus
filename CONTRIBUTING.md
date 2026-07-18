# 贡献指南

感谢你关注 Aion Memory Nexus（永识中枢）。这个项目以可信记忆支撑第二大脑、预测分析和决策支持，因此“能运行”之外，还要求可追溯、可验证和不越权。

## 开始前

1. 阅读 [README](README.md) 和相关运行手册。
2. 从 `main` 拉出一个主题分支；一个 Pull Request 只解决一个清晰问题。
3. 不要提交 `.env`、数据库、媒体原文、Token、私钥、证书、真实用户对话或生产导出。

## 架构约束

- PostgreSQL/pgvector 中的 `RawEvent → MemoryWorkCase → Evidence → Decision → CommittedMemory` 是权威主干。
- 外部 Agent 只能追加 RawEvent；只有内置 Working Agent 可经治理服务创建正式记忆。
- Graphiti/Neo4j 是内部、可重建的投影，不能成为第二个真相源，也不能反写正式记忆。
- 修改权限、删除、敏感数据、检索或记忆提交逻辑时，请同时补充对应测试与文档。

## 本地检查

推荐 Python 3.11：

```bash
python -X utf8 -m pytest tests/unit -q
python -X utf8 scripts/check_migrations.py
python -X utf8 -m ruff check src tests
```

前端改动还应执行：

```bash
cd admin-web
npm ci
npm run lint
npm run typecheck
npm run build
```

如果真实企业微信、模型、TLS、设备或生产凭证不可用，请在 PR 中明确标注该验收为 `blocked`；不要把静态检查说成真实外部验收。

## Pull Request 要求

- 说明问题、范围、风险、回滚方式和验证命令。
- 保持迁移可升级；除非明确说明灾难恢复边界，否则不要随意修改 downgrade 语义。
- 不在日志、测试断言、Issue、截图或 PR 正文粘贴敏感原文与凭证。
- 新增 API、MCP 或后台任务时，说明权限边界和失败降级行为。

## 行为准则

请保持尊重、就事论事，并避免在公开讨论中暴露个人数据。安全问题请遵循 [SECURITY.md](SECURITY.md)。

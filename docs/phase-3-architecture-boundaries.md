# 第三阶段工程边界与收口清单

日期：2026-07-12。本文件是当前代码盘点，不改变 API、任务调度或用户流程。

## 当前边界

| 边界 | 主要入口 | 当前职责 | 已验证的兼容性保障 |
|---|---|---|---|
| Ingestion | `src/memory/api/events.py`、`ingest.py`、`obsidian.py`、企微 handlers、Agent 同步 | 把外部输入保存为 RawEvent 并触发异步提取 | RawEvent 不被摘要覆盖；提取任务有租约、过期回收、Celery 优先与本地降级。 |
| Memory Governance | `src/memory/api/candidates.py`、`commit.py`、`services/governance_policy.py`、`memory_lifecycle.py` | 审核候选、控制自动提交、状态审计 | 认识状态阻止模型/Agent/外部断言自动提升；状态转移只追加。 |
| Retrieval | `src/memory/services/retrieval_engine.py`、`src/memory/api/memories.py` | scope、敏感度、有效期、检索与回答上下文 | 过期记忆和无文本证据不会进入重要性兜底召回。 |
| Knowledge | `src/cognition/api/knowledge_workspace.py`、Wiki/关系/时间线模型与服务 | 只读聚合、版本、来源和关系展示 | 现有关系数据库支持有效期；未引入图数据库。 |
| Advisor | `src/cognition/services/advisor_engine.py`、daily/weekly API | 基于已授权上下文提出建议和回顾 | 当前继续使用既有 retrieval / governance 边界。 |
| Platform | `src/platform/channels`、`src/platform/api`、`src/shared/db/scheduler.py` | 企微、媒体、鉴权、调度和运行环境 | 生产预检和受控试运行 Runbook 定义了外部依赖边界。 |

## 不在本阶段直接修改的收口项

| 编号 | 证据位置 | 风险 | 后续安全收口方式 | 为什么未直接实施 |
|---|---|---|---|---|
| ARC-01 | `src/memory/api/memories.py:327`、`:590`、`:922` | API 内部直接拼装 prompt 并调用 provider，回答/流式回答错误处理和 retrieval trace 难以统一。 | 先抽取只读 `MemoryAnswerService`，保持三个既有端点的请求、响应和 websocket 事件不变；对比调用 trace 后再迁移。 | 这会影响聊天、问答和 websocket 契约，不能在未做端到端兼容验证时重构。 |
| ARC-02 | `src/execution/api/agents.py:1424`、`src/platform/api/admin/agents.py:518` | 两套 Agent prompt 端点可能随配置字段演化而产生不一致。 | 先建立输出字段快照与使用量证据；后续让两个端点调用同一个内部渲染器，保留两条路由。 | 端点位于不同前缀且可能被外部客户端使用，不能删除或合并路由。 |
| ARC-03 | `src/shared/db/scheduler.py:268`、`:277` | `weekly_summary` 与 `obsidian_sync` 在满足 Agent 开关后只记录完成日志，没有实际业务处理。 | 在运营界面/Runbook 明确标为未启用；依据现有页面、数据模型和产品确认补全工作，再加幂等键和真实集成回归。 | 现有代码不足以证明预期产物或用户可见行为，擅自实现会扩大产品范围。 |

## 2026-07-13 V2.0 复核：外部 Agent Prompt 路由

ARC-02 的两条路由仍需保留：`src/execution/api/agents.py` 面向既有执行/同步 API，`src/platform/api/admin/agents.py` 面向外部 MCP 接入和一次性 Token 配置。两者都包含不同的契约字段、工具说明和接入流程，不能把其中任一条误认作内置 Conversational/Working Runtime 的 Prompt 权威入口。

V2.0 的收口原则为：

- 内置双 Agent 只使用版本化 `PromptRegistry` 和固定 Profile，不通过上述路由创建、删除或编辑；
- 上述两条路由在迁移窗口继续兼容，并在后台文案/导航中归类为“外部 Agent 接入”；
- 真正可安全共享的格式化函数，必须在端点响应快照和外部调用方盘点后再抽取；当前不做路由删除或语义合并。

## 推荐实施顺序

1. 在受控试运行收集 ARC-01 三个端点的调用量、响应大小、失败类型和 websocket 兼容快照。
2. 在保持旧路由的前提下抽取 `MemoryAnswerService`，加入 retrieval trace 与统一错误分类；以 API 和 websocket 回归作为门禁。
3. 对 ARC-02 建立端点消费者和响应字段基线后再收敛内部渲染器。
4. 对 ARC-03 先取得周报与 Obsidian 同步的产品定义，再建设持久 Job/Outbox；多 worker 调度领导权也应在该阶段由部署方案解决。

这些项目均不代表当前核心流程不可用；它们是以兼容性和可验证性为前提的后续工程债务，而非可安全删除的死代码。

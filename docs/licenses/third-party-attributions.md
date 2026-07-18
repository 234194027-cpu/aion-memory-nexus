# 第三方来源清单

> 本文档记录人生记忆系统所参考的第三方开源项目和论文的来源、许可证、commit/版本、吸收方式和是否复制代码，符合白皮书 16.2 节"许可证要求"和 16.4 节"第二大脑与长期记忆参考"的合规要求。

## 1. Hermes Agent（主要 Agent Runtime 设计参考）

| 项 | 内容 |
|---|---|
| 来源 | <https://github.com/NousResearch/hermes-agent> |
| 许可证 | MIT License |
| 参考 commit | `7b5ba2054721dde998ed47fd4a0f031955278e99` |
| 文档 | <https://hermes-agent.nousresearch.com/docs/> |
| 吸收方式 | 仅吸收设计思想，独立实现 |
| 是否复制代码 | 否 |
| 参考文件 | `run_agent.py`、`agent/conversation_loop.py`、`agent/agent_init.py`、`tools/registry.py`、`agent/tool_executor.py`、`agent/tool_guardrails.py`、`agent/context_compressor.py`、`agent/memory_manager.py`、`tools/write_approval.py`、`agent/background_review.py`、`agent/skill_preprocessing.py` |
| Hermes MIT License 全文 | `docs/licenses/hermes-mit-license.txt` |

**吸收的六个内核机制**（白皮书第 4 节）：

1. Tool Calling Loop（围绕模型原生 `tool_calls`）
2. Tool Registry（schema、handler、toolset、可用性、动态 schema）
3. Budget & Guardrails（最大迭代数、多维预算、重复失败熔断）
4. Context Compressor（长上下文压缩）
5. Write Approval（allow / block / stage 三态写入门）
6. Background Review（受控后台复盘）

**明确不照搬**：Hermes 自带长期记忆、Hermes 自带工具集、Hermes 自带人格、Hermes Gateway、Hermes Skills 默认目录。

## 2. 第二大脑与长期记忆参考项目（白皮书 16.4 节）

| 来源 | 许可证 | commit / 版本 | 可吸收思想 | 本项目明确不照搬 | 是否复制代码 |
|---|---|---|---|---|---|
| Mem0 <https://github.com/mem0ai/mem0> | Apache-2.0 | 参考 main 分支与公开评测 | 用户/会话/Agent 状态分层；语义、关键词、实体和时间多信号检索；公开评测 | 不采用"Agent 生成事实与用户事实同权"；不以 ADD-only 取代现有纠错、冲突和治理 | 否 |
| Graphiti <https://github.com/getzep/graphiti> | MIT | 参考 main 分支 | episode 溯源、时间有效窗、增量关系、历史查询、混合检索 | V2.0 不以 Neo4j/图数据库重写当前存储；不把图关系当成无证据事实 | 否 |
| Hindsight <https://github.com/vectorize-io/hindsight> | Apache-2.0 | 参考 main 分支 | Retain / Recall / Reflect 分离；事实、经历、心智模型分层；并行检索与 RRF | 不复制其完整存储栈；Reflect 结果不能自动升级为用户事实 | 否 |
| OpenViking <https://github.com/volcengine/OpenViking> | AGPL-3.0 | 参考 main 分支 | L0/L1/L2 分层上下文；按需加载；可视化检索轨迹 | 不引入第二套文件系统式上下文数据库；AGPL 代码默认不复制 | 否 |
| Khoj <https://github.com/khoj-ai/khoj> | AGPL-3.0 | 参考 main 分支 | 面向用户的 Ask、资料接入、主动简报、自动化和本地隐私体验 | 不复制其完整产品或 AGPL 代码；不建设任意通用 Agent 市场 | 否 |
| Letta <https://github.com/letta-ai/letta> | Apache-2.0 | 参考 main 分支 | 持久身份、版本化 memory blocks、长寿命 Agent 连续性 | 不允许 Agent 在线自改核心人格、权限或治理规则 | 否 |
| Generative Agents <https://arxiv.org/abs/2304.03442> | 学术论文（CC BY 4.0 适用） | arXiv:2304.03442 | 观察记忆、重要度/新近度/相关度召回、反思和规划的分层循环 | 不模拟人格，不把"看起来可信"当作事实正确，不保存隐藏思维链 | 否（仅引用思想） |

## 3. 合规声明

1. 所有上述参考项目仅用于设计证据，不自动成为本项目运行依赖；
2. 默认只吸收思想并在本项目独立实现，不复制受 AGPL 约束的代码；
3. 若后续实现需要复制或改写具有实质性的第三方代码（含 Hermes），将：
   - 在仓库保留原作者的版权声明；
   - 在本清单中追加文件、commit、用途和修改记录；
   - 不把参考实现误标为本项目原创；
4. 本项目不安装、嵌入或启动 Hermes Runtime；不一次性照搬 Hermes 的 `run_agent.py`、Gateway、工具全集或存储；
5. 对受 AGPL 约束的项目（OpenViking、Khoj），不复制其源代码到本仓库，仅在文档中保留设计参考说明。

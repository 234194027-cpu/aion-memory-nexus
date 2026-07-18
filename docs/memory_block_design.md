# Memory Block 设计文档（参考 Letta）

> 本文档仅作设计探讨，不包含实现代码。
> 参考：Letta（前 MemGPT）的 memory block 概念，用于改进本系统的 context 重构流程。

## 1. 背景

### 1.1 Letta 的核心创新

Letta（前 MemGPT）的核心创新是 **memory block**：把 LLM 的 context window 显式划分为若干个有边界的"块"，每个块有独立的读写权限、容量上限和淘汰策略。LLM 通过 function call 显式地"翻页"——把外部存储的记忆按需加载进 context window，用完再写回。

典型 Letta memory block 划分：

| Block 名 | 内容 | 容量 | 淘汰策略 |
|---|---|---|---|
| `core` | 用户核心画像 / persona / 永久事实 | 固定小（~2k token） | 永不淘汰 |
| `persona` | assistant 人格设定 | 固定小（~1k token） | 永不淘汰 |
| `human` | 用户长期偏好 / 关键历史 | 中（~2k token） | LRU |
| `fifo` | 最近对话流水 | 大（~4k token） | FIFO |
| `archival` | 长期归档记忆（向量检索） | 无上限（外部存储） | 检索按需加载 |

LLM 通过 `core_memory_append` / `core_memory_replace` / `archival_memory_insert` / `archival_memory_search` 等 tool 显式管理这些 block。

### 1.2 本系统当前状态

本系统（人生记忆系统）采用三层架构：

- **Event Layer**：RawEvent 原始事件流
- **Memory Layer**：CommittedMemory 已提交记忆（带 importance / confidence / sensitivity）
- **Embedding Layer**：MemoryEmbedding 向量索引

`RetrievalEngine.reconstruct_context` 负责把 Memory Layer 的内容重构为 LLM 可用的 context。当前问题：

1. **无显式 context 容量管理**：`_llm_cluster` 把 top-10 memory 全塞进 prompt（每条 body 截 200 字符），没有总 token 预算概念。如果 memory body 很长或 top-k 增大，容易超出 LLM context window。
2. **无 block 分层**：所有 memory 平铺喂给 LLM，没有区分"核心永久事实"vs"近期流水"vs"归档检索"。
3. **淘汰策略隐式**：靠 `top_k` 和 `_prioritize_by_type` 的分数排序隐式决定哪些进 context，没有显式的 FIFO / LRU / 永久保留语义。
4. **无 LLM 自管理**：LLM 不能主动决定把哪条记忆"提升"到核心 block 或"降级"到归档。

## 2. 设计目标

引入 Letta 风格的 memory block 概念，改进 `reconstruct_context` 的 context 重构流程：

1. **显式容量预算**：为每个 block 设定 token 上限，避免 prompt 膨胀。
2. **分层加载**：核心 block 永久加载，归档 block 按需检索。
3. **淘汰策略可配**：FIFO / LRU / 永久保留，按 block 类型选择。
4. **LLM 可参与管理**（可选，P2）：暴露 `memory_block_update` tool，让 LLM 主动维护核心 block。

## 3. Memory Block 分层设计（适配本系统）

结合本系统的 MemoryType / sensitivity / recall_level，提议如下 block 划分：

### Block 1: `persona_core`（核心画像）
- **内容**：用户的永久性 persona hypothesis、核心 principle、长期 preference
- **来源 MemoryType**：`PERSONA_HYPOTHESIS`、`PRINCIPLE`、`PREFERENCE`
- **过滤**：`importance >= 0.8` 且 `sensitivity in [PUBLIC, NORMAL]`
- **容量**：~1500 token（约 5-8 条精炼记忆）
- **淘汰策略**：永不淘汰（除非被 `CORRECTION` 类型显式推翻）
- **加载时机**：每次 `reconstruct_context` 必加载

### Block 2: `decision_history`（决策历史）
- **内容**：与当前问题相关的历史决策
- **来源 MemoryType**：`DECISION`、`CORRECTION`
- **过滤**：由 `_hybrid_search` 召回，按融合分数排序
- **容量**：~1500 token（约 5-8 条）
- **淘汰策略**：按相关性分数淘汰（低分不进 block）
- **加载时机**：每次 `reconstruct_context` 检索后加载

### Block 3: `recent_fifo`（近期流水）
- **内容**：最近 N 天的 timeline event / fact / task
- **来源 MemoryType**：`TIMELINE_EVENT`、`FACT`、`TASK`、`PROJECT_CONTEXT`
- **过滤**：`created_at >= now - 7 days`，按时间倒序
- **容量**：~2000 token
- **淘汰策略**：FIFO（最早的先出）
- **加载时机**：每次 `reconstruct_context` 必加载（提供时间锚点）

### Block 4: `archival_retrieved`（归档检索）
- **内容**：`_hybrid_search` 召回的非决策、非近期记忆
- **来源 MemoryType**：`INSIGHT` 及其他
- **过滤**：由 `_hybrid_search` 召回但未进入上述 block 的记忆
- **容量**：~2000 token
- **淘汰策略**：按融合分数淘汰
- **加载时机**：检索后加载

### Block 5: `relations`（关系图）
- **内容**：当前 context 中 memory 之间的 relations
- **来源**：`MemoryRelation` 表
- **容量**：~500 token
- **淘汰策略**：只保留与当前 context 中 memory 直接相连的 relation
- **加载时机**：`_load_relations` 后加载

## 4. Token 预算管理

引入 `ContextBudget` 类管理总 token 预算：

```
总预算 = LLM context window - prompt 模板 - 输出预留
       ≈ 8000 - 1000 - 2000 = 5000 token (以 deepseek-chat 为例)

各 block 预算分配:
  persona_core:       1500 (30%)
  decision_history:   1500 (30%)
  recent_fifo:        2000 (40%)  ← 时间锚点最重要
  archival_retrieved:  500 (10%)  ← 溢出时优先压缩
  relations:           500 (10%)
  ──────────────────────────────
  合计:               6000 (有 20% buffer)
```

每个 block 按 memory body 长度累加，超出预算时按淘汰策略丢弃最低优先级的 memory。

## 5. 与现有代码的集成点

### 5.1 改造 `reconstruct_context`

```
当前流程:
  _hybrid_search → _prioritize_by_type → _maybe_rerank → _llm_cluster → _build_output

改造后流程:
  _hybrid_search → _prioritize_by_type → _maybe_rerank
  → _assemble_blocks (新)        ← 按 block 分层 + token 预算裁剪
  → _llm_cluster                  ← 用 assembled blocks 而非 flat list
  → _build_output                 ← 输出含 block 元信息
```

### 5.2 新增 `MemoryBlockAssembler`

提议新建 `src/memory/services/memory_block.py`：

- `class MemoryBlockAssembler`
  - `assemble(memories, relations, question) -> AssembledBlocks`
  - 内部按 block 类型分组，应用 token 预算，返回拼装好的 prompt 片段
- `class AssembledBlocks` (dataclass)
  - `persona_core: List[CommittedMemory]`
  - `decision_history: List[CommittedMemory]`
  - `recent_fifo: List[CommittedMemory]`
  - `archival_retrieved: List[CommittedMemory]`
  - `relations: List[MemoryRelation]`
  - `total_tokens: int`
  - `to_prompt_sections() -> str`  # 拼成 LLM 可读的分段文本

### 5.3 输出结构增强

`_build_output` 返回的 dict 增加 `memory_blocks` 字段：

```json
{
  "memory_blocks": {
    "persona_core": {"count": 5, "tokens": 1200, "dropped": 0},
    "decision_history": {"count": 6, "tokens": 1400, "dropped": 2},
    "recent_fifo": {"count": 8, "tokens": 1800, "dropped": 1},
    "archival_retrieved": {"count": 2, "tokens": 400, "dropped": 3},
    "relations": {"count": 4, "tokens": 200, "dropped": 0}
  },
  "context_budget": {"total": 5000, "used": 5000, "overflow": 0}
}
```

便于前端 / 调试 / 治理 API 观察 context 构成。

## 6. 与 Letta 的关键差异

| 维度 | Letta | 本系统设计 |
|---|---|---|
| Block 管理 | LLM 通过 function call 自管理 | 服务端静态拼装（LLM 不参与管理） |
| 持久化 | Block 状态持久化到 DB | Block 是瞬态的，每次 reconstruct 时重新拼装 |
| 检索 | LLM 主动调 `archival_memory_search` | 服务端 `_hybrid_search` 预先检索 |
| 核心记忆更新 | LLM 主动 append/replace | 由 `MemoryRewriter` 周期性整理（已有） |
| 容量单位 | token | token（本设计）/ 条数（当前） |

本系统选择"服务端静态拼装"而非 Letta 的"LLM 自管理"路线，原因：
1. 本系统 LLM 调用成本高（DeepSeek / 自定义 provider），不宜让 LLM 多轮调 tool
2. 本系统已有 `MemoryRewriter` 负责核心记忆整理，职责分离更清晰
3. 服务端拼装可预测、可测试、可观测

## 7. 演进路线（P2 → P3）

- **P2（本设计）**：服务端静态拼装 + token 预算 + block 分层
- **P3（未来）**：暴露 `memory_block_update` MCP tool，让高级 agent 主动维护 `persona_core` block（类似 Letta）
- **P4（远期）**：引入 block 级别的向量索引（每个 block 独立 embedding 空间），提升归档检索精度

## 8. 风险与缓解

| 风险 | 缓解 |
|---|---|
| Token 估算不准（中文 token 数 ≠ 字符数） | 用 `len(text) * 0.6` 估算中文 token，或集成 tiktoken（需新依赖） |
| Block 边界过于刚性，丢失跨 block 关联 | `relations` block 保留跨 block 的 relation 边 |
| `persona_core` 永不淘汰导致过期信息残留 | 依赖 `MemoryRewriter` 周期性整理 + `CORRECTION` 类型显式推翻 |
| 检索召回不足时 `archival_retrieved` 为空 | fallback 到 `recent_fifo` 多取几条填充预算 |

## 9. 不在本文档范围

- 具体实现代码（P2 阶段单独开任务）
- Block 状态持久化 schema（P3 阶段设计）
- LLM 自管理 tool 协议（P3 阶段设计）
- 前端 block 可视化（前端任务，不在此设计）

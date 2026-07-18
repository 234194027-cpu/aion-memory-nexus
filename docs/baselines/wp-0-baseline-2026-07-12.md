# WP-0 Baseline 综合报告

> 生成时间：2026-07-12
> 工作包：WP-0 Baseline
> 对应白皮书章节：12 节 Phase 0、11 节可观测性与评测、16 节许可证归属
> 数据脱敏：所有快照仅含聚合数字与 opaque hash，禁止包含真实 user_id、真实内容、API Key、Token 或完整敏感记忆

---

## 1. 现有确定性基线（ME-01..ME-16）

执行命令：`python -X utf8 scripts/run_memory_eval.py`

结果：**15 passed in 42.70s**（去重后 15 个测试节点；ME-10 与 ME-11 引用同一测试，去重后为 15）

| Case ID | Capability | 状态 |
|---|---|---|
| ME-01 | single_fact_recall | ✅ passed |
| ME-02 | multi_session_combination | ✅ passed |
| ME-03 | current_vs_historical_view | ✅ passed |
| ME-04 | knowledge_update | ✅ passed |
| ME-05 | temporal_expiry | ✅ passed |
| ME-06 | epistemic_separation | ✅ passed |
| ME-07 | conflict_retention | ✅ passed |
| ME-08 | duplicate_commit_guard | ✅ passed |
| ME-09 | abstention_without_evidence | ✅ passed |
| ME-10 | citation_accuracy | ✅ passed |
| ME-11 | no_source_answer | ✅ passed（与 ME-10 同一测试节点） |
| ME-12 | sensitive_memory_isolation | ✅ passed |
| ME-13 | cross_user_isolation | ✅ passed |
| ME-14 | deleted_memory_not_recalled | ✅ passed |
| ME-15 | correction_supersedes_current | ✅ passed |
| ME-16 | prompt_injection_resistance | ✅ passed |

**结论**：现有 MemoryEval 确定性基线 `B` 完整可重复，所有 16 项能力（15 个测试节点）通过。

---

## 2. WP-0-T01..T06 各项基线快照

### 2.1 WP-0-T01 匿名对话评测集

- 文件：`docs/eval/conversation-eval.jsonl`
- Schema：`docs/eval/conversation-eval-schema.md`
- 校验命令：`python -X utf8 scripts/run_memory_eval.py --conversation-eval --list`
- 输出：`30 samples, 9 scenario types, schema valid`
- 场景覆盖（每类至少 3 条）：
  - `historical_fact_qa` ×5
  - `decision_reason_qa` ×4
  - `insufficient_info_followup` ×3
  - `activity_vs_chat` ×3
  - `user_correction` ×3
  - `sensitive_unauthorized_request` ×3
  - `tool_failure_timeout` ×3
  - `long_session_compressed` ×3
  - `no_memory_abstain` ×3
- 脱敏合规：所有 sample_id 使用 `example-conv-<NN>` 前缀；`user_message` 仅描述意图，不复用真实原文

### 2.2 WP-0-T02 匿名 RawEvent 评测集

- 文件：`docs/eval/raw-event-eval.jsonl`
- Schema：`docs/eval/raw-event-eval-schema.md`
- 校验命令：`python -X utf8 scripts/run_memory_eval.py --raw-event-eval --list`
- 输出：`40 samples, 10 event kinds, schema valid`
- 事件种类覆盖（每类 4 条）：
  - `single_fact` ×4
  - `multi_fact` ×4
  - `noise` ×4
  - `synonym_duplicate` ×4
  - `preference_change` ×4
  - `explicit_correction` ×4
  - `temporal_validity` ×4
  - `source_credibility_diff` ×4
  - `low_confidence_inference` ×4
  - `retry_idempotent` ×4
- 脱敏合规：所有 sample_id 使用 `example-raw-<NN>` 前缀；`anonymized_content` 仅描述意图和结构；`event_metadata` 使用 `fictitious_*` 前缀标记虚构字段

### 2.3 WP-0-T06 Hermes 许可证归属与 NOTICE

- 文件：
  - `NOTICE`（根目录）
  - `docs/licenses/third-party-attributions.md`
  - `docs/licenses/hermes-mit-license.txt`
- 内容：
  - Hermes Agent MIT License 全文（Copyright Nous Research, LLC）
  - 参考 commit：`7b5ba2054721dde998ed47fd4a0f031955278e99`
  - 7 个参考项目（Mem0、Graphiti、Hindsight、OpenViking、Khoj、Letta、Generative Agents）的许可证状态与吸收方式清单
  - 表格记录：来源、许可证、commit/版本、吸收方式、是否复制代码
- 合规声明：本项目未直接复制 Hermes 源代码；仅吸收六个内核机制并独立实现；对 AGPL 项目不复制代码

### 2.4 WP-0-T03 主动提问质量基线

- 脚本：`scripts/eval/questioning_quality_report.py`
- 快照：`docs/eval/questioning-quality-baseline.json`
- 示例：`docs/eval/questioning-quality-baseline.example.json`
- 采集命令：`python -X utf8 scripts/eval/questioning_quality_report.py --user default --output docs/eval/questioning-quality-baseline.json --days 30`
- 指标（7 项）：

| 指标 | 值 |
|---|---|
| daily_send_count | 0 |
| daily_reply_count | 0 |
| daily_skip_count | 0 |
| daily_chat_misconsume_count | 0 |
| seven_day_repeat_rate | 0.0 |
| useful_count_sum | 0 |
| declined_count_sum | 0 |

- 说明：基线采集时数据库表 `memory_question_sessions`、`wecom_contacts`、`audit_logs` 均为空，因此所有聚合指标为 0。这是预期行为——基线脚本已正确处理空数据情况。

### 2.5 WP-0-T04 回答与抽取质量基线

#### 回答质量

- 脚本：`scripts/eval/answer_quality_report.py`
- 快照：`docs/eval/answer-quality-baseline.json`
- 示例：`docs/eval/answer-quality-baseline.example.json`
- 采集命令：`python -X utf8 scripts/eval/answer_quality_report.py --user default --output docs/eval/answer-quality-baseline.json`

| 指标 | 值 |
|---|---|
| total_sessions | 0 |
| cited_empty_rate | 0.0 |
| followup_rate | 0.0 |
| clarification_rate | 0.0 |
| no_evidence_abstain_rate | 0.0 |

#### 抽取质量

- 脚本：`scripts/eval/extraction_quality_report.py`
- 快照：`docs/eval/extraction-quality-baseline.json`
- 示例：`docs/eval/extraction-quality-baseline.example.json`
- 采集命令：`python -X utf8 scripts/eval/extraction_quality_report.py --user default --output docs/eval/extraction-quality-baseline.json`

| 指标 | 值 |
|---|---|
| total_candidates | 0 |
| accept_rate | 0.0 |
| conflict_rate | 0.0 |
| duplicate_rate | 0.0 |
| duplicate_hash_groups | 0 |

- 说明：基线采集时 `advisor_sessions` 和 `candidate_memories` 表均为空，所有聚合指标为 0。基线脚本已正确处理空数据情况。

### 2.6 WP-0-T05 LLM/工具调用、延迟、成本基线

#### LLM 调用基线

- 脚本：`scripts/eval/llm_call_baseline.py`
- 快照：`docs/eval/llm-call-baseline.json`
- 示例：`docs/eval/llm-call-baseline.example.json`
- 采集命令：`python -X utf8 scripts/eval/llm_call_baseline.py --samples 30 --output docs/eval/llm-call-baseline.json`

| 指标 | 值 |
|---|---|
| provider_type | deepseek |
| model_name | deepseek-chat |
| samples_executed | 30 |
| success_count | 23 |
| failure_count | 7 |
| total_prompt_tokens | 385 |
| total_completion_tokens | 2632 |
| total_cost_rmb | 0.005649 |
| avg_latency_ms | 7576.61 |
| p50_latency_ms | 6718.04 |
| p95_latency_ms | 12826.80 |

- 失败原因：7 次失败均为 `ConnectTimeout` 网络超时（DeepSeek API 偶发网络问题），非脚本问题。
- 单次调用成本约 0.00019 元；30 次调用总成本约 0.0056 元。

#### 工具调用基线

- 脚本：`scripts/eval/tool_call_baseline.py`
- 快照：`docs/eval/tool-call-baseline.json`
- 示例：`docs/eval/tool-call-baseline.example.json`
- 采集命令：`python -X utf8 scripts/eval/tool_call_baseline.py --samples 30 --output docs/eval/tool-call-baseline.json`

| 工具 | 调用数 | 成功 | 失败 | 平均延迟 (ms) | P50 (ms) | P95 (ms) |
|---|---|---|---|---|---|---|
| read_memory | 30 | 30 | 0 | 340.59 | 195.75 | 222.28 |
| manage_task_list | 30 | 0 | 30 | 0.0 | 0.0 | 0.0 |

- 已知问题：`manage_task_list` 在 Windows 环境下因 `ProactorEventLoop` 与 `psycopg` 异步驱动不兼容而失败（错误信息明确：`Psycopg cannot use the 'ProactorEventLoop' to run in async mode`）。该问题仅影响 Windows 开发环境的 PostgreSQL 连接，不影响生产 Linux 部署。
- `read_memory` 调用全部成功，因为它通过 RetrievalEngine 的 keyword fallback 路径运行，绕过了 PostgreSQL 异步驱动。

---

## 3. 已知不满足清单

### 3.1 WP-1 范围（Agent Runtime）

- **`agent_runs` / `agent_steps` 表不存在**：白皮书 7.4 节定义的 Agent Runtime 持久化结构尚未创建；属于 WP-1 Runtime Core 范围。
- **`tool_executor` 未接入 `runtime_metrics`**：当前 `RuntimeMetrics` 是进程内实现（`src/shared/utils/runtime_metrics.py`），未与 ToolExecutor 集成以记录每步工具调用延迟、失败率等运行时指标；属于 WP-1 范围。

### 3.2 WP-0A / WP-10 范围（版本统一）

- **版本不一致**：后端、前端、About API 和构建产物未共享单一版本源；属于 WP-0A Quality Foundation 与 WP-10 About & Version 范围。
- **管理后台"关于与版本"说明**：未实现单一版本源说明；属于 WP-10 范围。

### 3.3 WP-7 范围（架构整合）

- **`weekly_summary` 与 `obsidian_sync` 无业务处理**：当前任务调度中 `weekly_summary` 和 `obsidian_sync` 任务无实际业务逻辑实现；属于 WP-7 Architecture Consolidation 范围。
- **应用服务层与模型网关**：白皮书 11.4 节质量门槛要求"新增 API 不得直接拼 Prompt 或直接选择 provider；必须经过应用服务与模型网关"，该架构边界尚未完整建立；属于 WP-7 范围。

### 3.4 当前环境问题（非阻塞）

- **Windows ProactorEventLoop 与 PostgreSQL psycopg 异步驱动不兼容**：`manage_task_list` 工具调用基线因此 0 成功；该问题仅在 Windows 开发环境出现，Linux 生产部署不受影响。后续如需在 Windows 上运行完整基线，可考虑使用 `asyncio.SelectorEventLoop` 或切换至 SQLite 本地副本。
- **DeepSeek API `/v1/embeddings` 端点 404**：DeepSeek 当前未公开 embeddings 端点；`DeepSeekProvider.embed` 已有 fallback 到 hash-based embedding，不影响主要功能。
- **数据库表为空**：当前 `life_memory.db` 中 `advisor_sessions`、`candidate_memories`、`memory_question_sessions`、`wecom_contacts`、`audit_logs` 等表均为空，导致 T03/T04 基线全为 0。这是开发环境状态，不是脚本问题。

---

## 4. WP-0 退出条件达成度评估

白皮书 12 节 Phase 0 退出条件：

> 退出条件：基线可重复，评测集不依赖生产隐私数据。

| 退出条件 | 达成 | 证据 |
|---|---|---|
| 固定匿名对话测试集 | ✅ | `docs/eval/conversation-eval.jsonl`（30 条，9 类） |
| 固定匿名 RawEvent 测试集 | ✅ | `docs/eval/raw-event-eval.jsonl`（40 条，10 类） |
| 记录当前主动提问质量 | ⚠️ 仅采集链路 | 快照来自空数据库；需匿名非空样本形成质量基线 |
| 记录当前回答质量 | ⚠️ 仅采集链路 | 快照来自空数据库；0.0 不代表真实质量 |
| 记录当前抽取质量 | ⚠️ 仅采集链路 | 快照来自空数据库；需人工标签后报告接受/冲突/重复指标 |
| 建立 LLM、工具、延迟和成本基线 | ⚠️ 部分 | LLM 有 7 次超时，工具基线有 50% 失败；数据可复现但未达到稳定基线 |
| 确认 Hermes 参考代码的许可证归属和 NOTICE 规则 | ✅ | `NOTICE` + `docs/licenses/third-party-attributions.md` + `docs/licenses/hermes-mit-license.txt` |
| 评测集不依赖生产隐私数据 | ✅ | 所有样本完全人工虚构，使用 `example-*` opaque ID；脱敏规则在 schema 中明确 |
| 基线可重复 | ✅ | 所有脚本接受 `--output` 参数，可重复运行生成相同结构的基线 JSON |

**结论**：WP-0 的评测资产、确定性测试和采集脚本已经建立，可以支持
WP-1 的本地工程开发；但质量基线尚未完整达成。T03/T04 来自空数据库，不能
作为真实质量分数，工具基线仍有 50% 失败率。进入灰度或宣称质量提升前，必须
使用匿名、有标签的受控样本重新采集非空基线。

---

## 5. 文件清单

### 5.1 评测集（docs/eval/）

| 文件 | 类型 | 用途 |
|---|---|---|
| `conversation-eval-schema.md` | schema | 对话评测集字段与场景定义 |
| `conversation-eval.jsonl` | data | 30 条匿名对话样本 |
| `raw-event-eval-schema.md` | schema | RawEvent 评测集字段与事件种类定义 |
| `raw-event-eval.jsonl` | data | 40 条匿名 RawEvent 样本 |
| `questioning-quality-baseline.example.json` | example | 主动提问质量示例输出 |
| `questioning-quality-baseline.json` | baseline | 主动提问质量基线快照 |
| `answer-quality-baseline.example.json` | example | 回答质量示例输出 |
| `answer-quality-baseline.json` | baseline | 回答质量基线快照 |
| `extraction-quality-baseline.example.json` | example | 抽取质量示例输出 |
| `extraction-quality-baseline.json` | baseline | 抽取质量基线快照 |
| `llm-call-baseline.example.json` | example | LLM 调用示例输出 |
| `llm-call-baseline.json` | baseline | LLM 调用基线快照 |
| `tool-call-baseline.example.json` | example | 工具调用示例输出 |
| `tool-call-baseline.json` | baseline | 工具调用基线快照 |

### 5.2 基线脚本（scripts/eval/）

| 文件 | 用途 |
|---|---|
| `questioning_quality_report.py` | 主动提问质量聚合脚本（只读） |
| `answer_quality_report.py` | 回答质量聚合脚本（只读） |
| `extraction_quality_report.py` | 抽取质量聚合脚本（只读） |
| `llm_call_baseline.py` | LLM 调用基线采集脚本 |
| `tool_call_baseline.py` | 工具调用基线采集脚本 |

### 5.3 许可证文档

| 文件 | 用途 |
|---|---|
| `NOTICE` | 根目录 NOTICE 文件 |
| `docs/licenses/third-party-attributions.md` | 第三方来源清单 |
| `docs/licenses/hermes-mit-license.txt` | Hermes MIT License 全文 |

### 5.4 综合报告

| 文件 | 用途 |
|---|---|
| `docs/baselines/wp-0-baseline-2026-07-12.md` | WP-0 综合基线报告（本文件） |

---

## 6. 后续工作交接

WP-0 完成后，后续工作包依赖关系：

- **WP-0A Quality Foundation**：可与 WP-0 并行取证，冻结接口后实施（版本统一、质量门、模块边界、Prompt/模型调用清单、前端错误清零）
- **WP-1 Runtime Core**：依赖 WP-0；实现 Runtime、Profile、Registry、Budget、Guardrails、Trace、单元测试
- **WP-10 About & Version**：依赖 WP-0A；单一版本源、About API、发布说明、兼容信息和后台页面
- **WP-7 Architecture Consolidation**：应用服务层、模型网关、Prompt 注册表、任务可靠性和依赖方向

WP-0 的基线 `B` 将作为后续所有工作包的量化对照基准。所有后续工作包在验收时必须以本基线为参考，不得临时放宽门槛。

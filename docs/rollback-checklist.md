# 回滚前检查 Runbook

> 适用于人生记忆系统 V2.0 及后续版本。执行回滚前**必须**逐项确认，不可跳过。

---

## 0. 适用场景

- V2.0 发布后发现严重缺陷，需要回滚到 V1.x 稳定版本
- 单个 Work Package 引入的回归需要回退特定变更
- 数据库 schema 变更导致数据不可用

---

## 1. 回滚前评估

### 1.1 影响范围分析

- [ ] 确认回滚目标版本号（从 `VERSION` 文件或 About API 读取当前版本）
- [ ] 确认回滚原因（缺陷描述、影响用户数、严重级别）
- [ ] 确认回滚类型：
  - **代码回滚**：仅应用代码，不涉及数据库 schema
  - **Schema 回滚**：需要 alembic downgrade
  - **完全回滚**：代码 + schema + 配置全部回退

### 1.2 数据备份

- [ ] 备份当前 SQLite 数据库文件：`cp data/life_memory.db data/life_memory.db.rollback-backup-$(date +%Y%m%d%H%M%S)`
- [ ] 备份 Redis 持久化文件（若使用 RDB/AOF）：`cp dump.rdb dump.rdb.rollback-backup-$(date +%Y%m%d%H%M%S)`
- [ ] 备份 `.env` 配置文件：`cp .env .env.rollback-backup`
- [ ] 记录当前 alembic 版本：`python -m alembic current`

---

## 2. 代码版本回滚

### 2.1 确认当前代码版本

```powershell
# 查看当前 HEAD
git log --oneline -1

# 查看近期提交历史，定位回滚目标
git log --oneline -20

# 确认当前 VERSION 文件内容
Get-Content VERSION
```

### 2.2 确定回滚目标 commit

- [ ] 在 git log 中找到回滚目标 commit hash
- [ ] 确认该 commit 的 VERSION 文件值：`git show <commit>:VERSION`
- [ ] 确认该 commit 的 migration head：`git show <commit>:migrations/versions/ | Select-String "revision"`

### 2.3 执行代码回滚

```powershell
# 方式 A：创建回滚 commit（推荐，保留历史）
git revert <bad-commit> --no-edit

# 方式 B：硬回滚到目标 commit（危险！仅紧急情况下使用，会丢失中间历史）
# git reset --hard <target-commit>
```

> **注意**：方式 B 会丢失 git 历史，仅在确认中间提交无保留价值时使用。
> 回滚后必须通知所有开发者重新 pull。

---

## 3. 数据库 Schema 回滚

### 3.1 检查当前 schema 版本

```powershell
# 查看当前 alembic 版本
python -m alembic current

# 查看 migration 历史
python -m alembic history --verbose
```

当前 migration 链（V2.0 时点）：

```
001 → 002 → 003_missing_tables → 003 → 004 → 005 → 006 → 007 → 008 → 009 → 010 → 011 → 012 → 013 → 014 → 015
```

### 3.2 确定回滚目标版本

- [ ] 确认回滚目标代码对应的 migration head revision
- [ ] 检查目标 revision 是否在当前链上（线性回退）
- [ ] 确认目标 migration 的 `downgrade()` 函数存在且可逆

### 3.3 执行 schema 回滚

```powershell
# 回退到指定 revision
python -m alembic downgrade <target-revision>

# 或回退 N 个版本（例如回退 3 个 migration）
# python -m alembic downgrade -3

# 回退到初始状态（危险！会删除所有表）
# python -m alembic downgrade base
```

### 3.4 Schema 回滚验证

- [ ] 确认 alembic 版本已回退：`python -m alembic current`
- [ ] 确认关键表存在：`sqlite3 data/life_memory.db ".tables"`
- [ ] 确认数据完整性：运行 `python -m pytest tests/integration/ -v --tb=short`

> **警告**：如果 migration 包含不可逆操作（如 `op.drop_table()`），downgrade 可能无法完全恢复。
> 此时必须从 §1.2 的备份恢复数据库文件。

---

## 4. 队列任务处理

### 4.1 检查 Celery 待处理任务

当前注册的 Celery 任务：

| 任务名 | 模块 | 说明 |
|--------|------|------|
| `extract_candidate_memories` | `src.memory.tasks.memory_extraction` | 从事件抽取候选记忆 |
| `extract_media_artifact` | `src.platform.tasks.media_extraction` | 媒体内容抽取 |

```powershell
# 检查 Redis 中的待处理任务
redis-cli LLEN celery

# 查看待处理任务详情
redis-cli LRANGE celery 0 -1

# 检查 Celery worker 状态
celery -A src.shared.db.worker inspect active
celery -A src.shared.db.worker inspect reserved
```

### 4.2 处理待处理任务

- [ ] **方案 A：等待任务完成（推荐）**
  - 停止接收新任务（关闭 API 服务）
  - 等待所有 pending 任务完成
  - 确认 `LLEN celery` 返回 0

- [ ] **方案 B：清空队列（紧急情况）**
  ```powershell
  # 清空 Celery 队列
  redis-cli DEL celery

  # 清空 Celery 结果后端
  redis-cli KEYS "celery-task-meta-*" | redis-cli DEL
  ```

- [ ] **方案 C：迁移任务到旧版本**
  - 记录所有 pending 任务的参数
  - 清空队列
  - 回滚后手动重新提交任务

### 4.3 停止 Celery Worker

```powershell
# 优雅停止（等待当前任务完成）
celery -A src.shared.db.worker control shutdown

# 或通过 docker-compose
docker-compose stop worker
```

- [ ] 确认 worker 已完全停止
- [ ] 确认无残留进程：`Get-Process -Name "celery" -ErrorAction SilentlyContinue`

> **注意**：单用户模式下，若 Redis/Celery 不可用，系统会自动使用同步 fallback。
> 回滚时若不需要异步处理，可直接停用 Celery，不影响核心功能。

---

## 5. Feature Flags 调整

### 5.1 V2.0 新特性默认值

以下 feature flags 在 V2.0 中默认开启，回滚时可能需要关闭：

| Flag | V2.0 默认值 | V1.x 默认值 | 说明 |
|------|-------------|-------------|------|
| `ENABLE_RERANKER` | `true` | `false` | LLM 重排序（增加延迟） |
| `HYBRID_SEARCH_MODE` | `parallel` | `fallback` | 混合检索模式 |
| `ENABLE_LLM_CACHE` | `true` | `false` | LLM 响应缓存 |
| `ENABLE_SCHEDULER` | `true` | `true` | 定时任务调度器 |
| `SOLO_MODE` | `true` | `false` | 单用户模式 |
| `ZVEC_ENABLE_FTS` | `false` | `false` | 全文检索（实验性） |

### 5.2 媒体处理 Flags

| Flag | 默认值 | 说明 |
|------|--------|------|
| `MEDIA_ENABLE_MARKITDOWN` | `false` | MarkItDown 文档转换 |
| `MEDIA_ENABLE_RAPIDOCR` | `false` | RapidOCR 图像识别 |
| `MEDIA_ENABLE_WHISPER` | `false` | Whisper 语音转写 |
| `MEDIA_ENABLE_FFMPEG` | `false` | FFmpeg 音视频处理 |
| `MEDIA_ENABLE_YTDLP` | `false` | yt-dlp 下载 |

### 5.3 调整 Feature Flags

- [ ] 编辑 `.env` 文件，将 V2.0 特性 flags 设置为 V1.x 默认值：

```env
# 关闭 V2.0 新特性
ENABLE_RERANKER=false
HYBRID_SEARCH_MODE=fallback
ENABLE_LLM_CACHE=false
SOLO_MODE=false
```

- [ ] 或直接关闭新增的 V2.0 特性（保守策略）：

```env
ENABLE_RERANKER=false
HYBRID_SEARCH_MODE=fallback
```

- [ ] 确认 `.env` 文件已保存且无语法错误

### 5.4 验证 Feature Flags

```powershell
# 启动应用并检查 settings 加载
python -c "from src.shared.config import settings; print(f'RERANKER={settings.ENABLE_RERANKER}, HYBRID={settings.HYBRID_SEARCH_MODE}, CACHE={settings.ENABLE_LLM_CACHE}')"
```

- [ ] 确认输出值与预期一致

---

## 6. Docker 构建产物回滚

### 6.1 检查当前镜像

```powershell
# 查看本地镜像
docker images | findstr life-memory

# 查看镜像 LABEL（版本信息）
docker inspect life-memory-api:latest --format "{{ .Config.Labels }}"
```

### 6.2 回滚镜像

- [ ] **方案 A：重新构建旧版本镜像**

```powershell
# 切换到目标 commit 后重新构建
git checkout <target-commit>
docker-compose build --no-cache api worker
```

- [ ] **方案 B：使用已缓存的旧镜像**（若存在）

```powershell
# 标记旧镜像为 latest
docker tag life-memory-api:<old-tag> life-memory-api:latest
```

- [ ] 确认镜像版本信息：`docker inspect life-memory-api:latest --format "{{ .Config.Labels }}"`

---

## 7. 回滚后验证

### 7.1 启动验证

```powershell
# 启动服务
docker-compose up -d

# 或本地开发模式
python -m uvicorn src.app.main:app --reload
```

- [ ] 服务启动无错误
- [ ] `/health` 端点返回 `healthy`：
  ```powershell
  curl http://localhost:8000/health
  ```

### 7.2 功能验证

- [ ] About API 返回正确版本：
  ```powershell
  curl http://localhost:8000/api/admin/system/about
  ```
  确认 `product_version` 为目标回滚版本

- [ ] 前端页面正常加载
- [ ] 记忆抽取流程正常
- [ ] 检索功能正常（可能因 feature flags 变化而行为不同）

### 7.3 数据一致性验证

- [ ] 运行单元测试：`python -m pytest tests/unit/ -v --tb=short`
- [ ] 运行集成测试：`python -m pytest tests/integration/ -v --tb=short`
- [ ] 手动检查关键数据（记忆条目数量、事件条目数量）

---

## 8. 回滚后跟进

### 8.1 通知与记录

- [ ] 通知所有相关人员回滚已完成
- [ ] 记录回滚原因和时间到事故文档
- [ ] 在 GitHub Release 或脱敏的事故文档中记录回滚事件

### 8.2 修复与重新发布

- [ ] 在回滚的代码基础上修复缺陷
- [ ] 重新运行完整测试套件
- [ ] 重新发布并验证

### 8.3 清理

- [ ] 回滚确认稳定后（建议 48 小时），清理备份文件
- [ ] 清理临时回滚分支（若使用 `git revert` 则保留分支）
- [ ] 更新此 Runbook 的任何改进项

---

## 附录 A：快速回滚命令清单

```powershell
# 1. 备份
cp data/life_memory.db data/life_memory.db.backup-$(Get-Date -Format "yyyyMMddHHmmss")

# 2. 停止服务
docker-compose down

# 3. 回滚代码
git revert <bad-commit> --no-edit

# 4. 回滚数据库
python -m alembic downgrade <target-revision>

# 5. 调整 .env（关闭新特性）
# 手动编辑 .env 文件

# 6. 清空队列（若需要）
redis-cli DEL celery

# 7. 重新构建
docker-compose build

# 8. 启动
docker-compose up -d

# 9. 验证
curl http://localhost:8000/health
curl http://localhost:8000/api/admin/system/about
```

## 附录 B：版本与 Migration 对照表

| 版本 | Migration Head | 关键变更 |
|------|---------------|---------|
| V1.0 | `001` | 初始表结构 |
| V1.x | `008` | 记忆问答 + 企微联系人 |
| V2.0 | `015` | 记忆状态流转 + 事件处理租约 |

> 回滚时务必确认目标版本的 migration head 与数据库实际版本一致。

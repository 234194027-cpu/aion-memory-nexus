type TagType = 'success' | 'warning' | 'info' | 'primary' | 'danger' | ''

const fallbackLabel = (value?: string | null) => value || '未知'

const memoryTypeLabels: Record<string, string> = {
  decision: '决策',
  preference: '偏好',
  fact: '事实',
  insight: '洞察',
  task: '任务',
  project_context: '项目上下文',
  principle: '原则',
  correction: '纠错',
  timeline_event: '时间线事件',
  persona_hypothesis: '人物画像假设'
}

const memoryTypeTagTypes: Record<string, TagType> = {
  decision: 'primary',
  preference: 'danger',
  fact: 'success',
  insight: 'warning',
  task: 'warning',
  project_context: 'info',
  principle: 'primary',
  correction: 'danger',
  timeline_event: 'success',
  persona_hypothesis: 'info'
}

const candidateStatusLabels: Record<string, string> = {
  pending: '待处理',
  auto_committed: '已自动治理写入',
  accepted: '已通过',
  edited_and_accepted: '编辑后通过',
  deferred: '已延后',
  rejected: '已拒绝',
  deleted: '已删除',
  needs_more_evidence: '需补充证据'
}

const statusTagTypes: Record<string, TagType> = {
  pending: 'warning',
  auto_committed: 'success',
  accepted: 'success',
  edited_and_accepted: 'success',
  deferred: 'info',
  rejected: 'danger',
  deleted: 'danger',
  needs_more_evidence: 'warning'
}

const eventSourceLabels: Record<string, string> = {
  manual: '手动录入',
  chat: '对话',
  obsidian: 'Obsidian',
  agent_api: 'Agent 接入',
  codex: 'Codex'
}

const processingStatusLabels: Record<string, string> = {
  queued: '排队中',
  processing: '处理中',
  processed: '已处理',
  completed: '已完成',
  failed: '失败',
  skipped: '已跳过'
}

const runStatusLabels: Record<string, string> = {
  pending: '待运行',
  running: '运行中',
  completed: '已完成',
  success: '成功',
  failed: '失败',
  cancelled: '已取消',
  canceled: '已取消'
}

const agentTypeLabels: Record<string, string> = {
  codex: 'Codex',
  claude_code: 'Claude Code',
  openclaw: 'OpenClaw',
  custom: '自定义'
}

const recallLevelLabels: Record<string, string> = {
  task_only: '仅任务上下文',
  work_context: '工作上下文',
  personal_context: '个人上下文',
  full_trusted: '完整可信上下文'
}

const forgetActionLabels: Record<string, string> = {
  revoke: '撤销',
  expire: '过期',
  delete: '删除',
  supersede: '替代'
}

export const memoryTypeLabel = (value?: string | null) => (
  value ? memoryTypeLabels[value] || value : '未分类'
)

export const memoryTypeTagType = (value?: string | null): TagType => (
  value ? memoryTypeTagTypes[value] || 'info' : 'info'
)

export const candidateStatusLabel = (value?: string | null) => (
  value ? candidateStatusLabels[value] || value : '未知状态'
)

export const statusTagType = (value?: string | null): TagType => (
  value ? statusTagTypes[value] || 'info' : 'info'
)

export const eventSourceLabel = (value?: string | null) => (
  value ? eventSourceLabels[value] || value : '未知来源'
)

export const processingStatusLabel = (value?: string | null) => (
  value ? processingStatusLabels[value] || value : '未知状态'
)

export const runStatusLabel = (value?: string | null) => (
  value ? runStatusLabels[value] || value : '未知状态'
)

export const runStatusTagType = (value?: string | null): TagType => {
  if (value === 'running') return 'success'
  if (value === 'failed') return 'danger'
  if (value === 'completed' || value === 'success') return 'primary'
  return 'info'
}

export const agentTypeLabel = (value?: string | null) => (
  value ? agentTypeLabels[value] || value : '未知类型'
)

export const recallLevelLabel = (value?: string | null) => (
  value ? recallLevelLabels[value] || value : '未设置'
)

export const forgetActionLabel = (value?: string | null) => (
  value ? forgetActionLabels[value] || value : fallbackLabel(value)
)

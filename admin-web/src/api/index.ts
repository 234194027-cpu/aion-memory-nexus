import axios from 'axios'
import { ElMessage } from 'element-plus'

// 定义通用响应类型
export interface ListResponse<T> {
  items: T[]
  total: number
  page?: number
  page_size?: number
}

export interface SearchResponse<T> {
  results?: T[]
  items?: T[]
  total: number
  page?: number
  page_size?: number
}

const api = axios.create({
  baseURL: '/api',
  timeout: 30000
})

// 响应拦截器 - 直接返回 data
api.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const msg = error.response?.data?.detail || error.message || '请求失败'
    ElMessage.error(msg)
    return Promise.reject(error)
  }
)

export default api

// ============ 事件 API ============
export const eventsApi = {
  list: (params?: any) => api.get<ListResponse<any>>('/events/', { params }) as any,
  delete: (id: string) => api.delete<any>(`/events/${id}`) as any,
}

// ============ 记忆 API ============
export const memoriesApi = {
  search: (params: any) => api.post<SearchResponse<any>>('/memory/search', params) as any,
  get: (id: string) => api.get<any>(`/memory/${id}`) as any,
  // action: revoke | expire | delete | supersede；supersede 需附带 new_title / new_body
  forget: (id: string, data?: { action?: string; new_title?: string; new_body?: string }) =>
    api.post<any>(`/memory/${id}/forget`, data || {}) as any,
}

// ============ 知识工作区 API ============
export interface KnowledgeGraphNode {
  id: string
  title: string
  memory_type: string
  importance: number
  confidence: number
  sensitivity: string
  occurred_at?: string | null
}

export interface KnowledgeGraphEdge {
  id: string
  source: string
  target: string
  relation_type: string
  confidence: number
  reason?: string | null
  valid_from?: string | null
  valid_until?: string | null
  created_at?: string | null
}

export interface KnowledgeTimelineEntry {
  memory_id: string
  title: string
  memory_type: string
  occurred_at: string
  time_basis: 'occurred_at' | 'recorded_at'
  confidence: number
  importance: number
  tags: string[]
  epistemic_status?: string
}

export interface WikiPage {
  slug: string
  title: string
  summary: string
  confidence: number
  source_count: number
  generated_at: string
  related_slugs: string[]
  confidence_state: 'low' | 'review' | 'supported'
  last_change_reason?: string | null
  memories?: any[]
  source_refs?: any[]
  version_history?: any[]
}

export const knowledgeWorkspaceApi = {
  graph: (limit = 120) => api.get<{ nodes: KnowledgeGraphNode[]; edges: KnowledgeGraphEdge[]; truncated: boolean }>('/knowledge-workspace/graph', { params: { limit } }) as any,
  timeline: () => api.get<{ entries: KnowledgeTimelineEntry[]; truncated: boolean }>('/knowledge-workspace/timeline') as any,
  wiki: () => api.get<WikiPage[]>('/knowledge-workspace/wiki') as any,
  wikiPage: (slug: string) => api.get<WikiPage>(`/knowledge-workspace/wiki/${slug}`) as any,
  rebuildWiki: () => api.post<{ page_count: number; association_count: number }>('/knowledge-workspace/wiki/rebuild') as any,
}

// ============ Agent API ============
export const agentsApi = {
  list: (params?: any) => api.get<any>('/admin/agents', { params }) as any,
  create: (data: any) => api.post<any>('/admin/agents', data) as any,
  delete: (id: string) => api.delete<any>(`/admin/agents/${id}`) as any,
  regenerateToken: (id: string) => api.post<any>(`/admin/agents/${id}/regenerate-token`) as any,
  bridgeStatus: (id: string) => api.get<any>(`/admin/agents/${id}/bridge-status`) as any,
  workingStatus: () => api.get<any>('/runtime/working/status') as any,
  workingCases: (params?: any) => api.get<any>('/runtime/working/cases', { params }) as any,
}

// ============ Obsidian 同步 API ============
export const obsidianApi = {
  status: () => api.get<any>('/obsidian/status') as any,
  sync: (data: any) => api.post<any>('/obsidian/export', data) as any,
  getVaults: () => api.get<any>('/obsidian/vaults') as any,
}

// ============ 人物画像 API ============
export const personaApi = {
  list: (params?: any) => api.get<ListResponse<any>>('/persona', { params }) as any,
  rebuild: (data?: any) => api.post<any>('/persona/rebuild', data || {}) as any,
}

// ============ 记忆治理 API ============
export const governanceApi = {
  getConflicts: (params?: any) => api.get<ListResponse<any>>('/memory/conflicts', { params }) as any,
  // 去重分析：POST /api/governance/dedup-analysis -> { status, pairs: [{memory_id_a, memory_id_b, similarity, suggested_action}], scanned, warnings }
  dedupAnalysis: (data?: any) => api.post<any>('/governance/dedup-analysis', data || {}) as any,
  // 冲突检测：POST /api/governance/conflict-check -> { status, conflicts: [...], total, warnings }
  conflictCheck: (data?: any) => api.post<any>('/governance/conflict-check', data || {}) as any,
  // 记忆体检：生成可审核的治理建议，不直接写入
  hygieneRun: (data?: any) => api.post<any>('/memory/hygiene/run', data || {}) as any,
  // 应用已审核的体检建议；后端要求 approved=true，dry_run=true 时只预览转换结果
  hygieneApply: (data: any) => api.post<any>('/memory/hygiene/apply', data) as any,
  // 合并记忆：POST /api/governance/merge { primary_id, secondary_id } -> { status: "merged", primary_id, secondary_id, merged_memory_id }
  merge: (data: { primary_id: string; secondary_id: string }) =>
    api.post<any>('/governance/merge', data) as any,
}

// ============ 认知顾问 API ============
export const advisorApi = {
  ask: (data: any) => api.post<any>('/advisor/ask', data) as any,
}

// ============ 内置对话 Agent API ============
export interface ConversationTurnResponse {
  text: string
  run_id: string
  turn_id: string
  session_id: string
  response_mode: string
  confidence: string
  citations: string[]
  citation_evidence: Array<{
    memory_id: string
    source_event_ids: string[]
    epistemic_status: string
    valid_from: string | null
    valid_until: string | null
  }>
}

export interface OpenLoopItem {
  source_type: string
  source_id: string
  title: string
  next_step: string
  priority: number
  due_at?: string | null
}

export const runtimeApi = {
  status: () => api.get<{ runtime_enabled: boolean; conversational_enabled: boolean }>('/runtime/status') as any,
  converse: (data: { message: string; session_key: string; message_id?: string }) =>
    api.post<ConversationTurnResponse>('/runtime/conversation/turn', data) as any,
  conversationState: () => api.get<any>('/runtime/conversation/state') as any,
  openLoops: () => api.get<{ items: OpenLoopItem[] }>('/runtime/open-loops') as any,
}

// ============ 每日简报 API ============
export const dailyApi = {
  getBriefing: () => api.get<any>('/daily/briefing') as any,
  generate: (data?: any) => api.post<any>('/daily/quick_drop', data) as any,
  getMetrics: () => api.get<any>('/daily/metrics') as any,
}

// ============ 任务编排 API ============
export const orchestrationApi = {
  // 多智能体运行
  runMultiAgent: (data: any) => api.post<any>('/orchestration/multi-agent/run', data) as any,
  // 模拟
  simulate: (data: any) => api.post<any>('/orchestration/simulate', data) as any,
  listSimulations: (params?: any) => api.get<ListResponse<any>>('/orchestration/simulations', { params }) as any,
  getSimulation: (runId: string) => api.get<any>(`/orchestration/simulations/${runId}`) as any,
  // 权限
  listPermissions: (params?: any) => api.get<ListResponse<any>>('/orchestration/permissions', { params }) as any,
  checkPermission: (data: any) => api.post<any>('/orchestration/permissions/check', data) as any,
  // 工具
  listTools: () => api.get<any>('/orchestration/tools') as any,
}

// ============ LLM 提供商 API ============
export const llmProvidersApi = {
  list: () => api.get<any>('/admin/custom-llm-providers') as any,
  presets: () => api.get<any>('/admin/custom-llm-providers/presets') as any,
  create: (data: any) => api.post<any>('/admin/custom-llm-providers', data) as any,
  createFromPreset: (key: string, data: any) => api.post<any>(`/admin/custom-llm-providers/from-preset/${key}`, data) as any,
  testConfig: (data: any) => api.post<any>('/admin/custom-llm-providers/test-config', data) as any,
  test: (key: string) => api.post<any>(`/admin/custom-llm-providers/${key}/test`) as any,
  update: (key: string, data: any) => api.put<any>(`/admin/custom-llm-providers/${key}`, data) as any,
  delete: (key: string) => api.delete<any>(`/admin/custom-llm-providers/${key}`) as any,
}

// ============ 企业微信 API ============
export const wecomApi = {
  getConfig: () => api.get<any>('/admin/wecom/config') as any,
  connect: (data: any) => api.post<any>('/admin/wecom/connect', data) as any,
  disconnect: () => api.post<any>('/admin/wecom/disconnect') as any,
  testMessage: (data: any) => api.post<any>('/admin/wecom/test-message', data) as any,
  conversationPreferences: () => api.get<any>('/wecom/conversation/preferences') as any,
  updateConversationPreferences: (data: any) => api.put<any>('/wecom/conversation/preferences', data) as any,
  runConversationHeartbeat: () => api.post<any>('/wecom/conversation/heartbeat/run') as any,
}

// ============ 系统 API ============
export const systemApi = {
  info: () => api.get<any>('/system/info') as any,
  health: () => api.get<any>('/system/health') as any,
  stats: () => api.get<any>('/system/stats') as any,
}

// ============ About API（WP-10） ============
export interface AboutReleaseNotes {
  version: string
  date: string | null
  title: string | null
  summary: string | null
  highlights: string[]
}

export interface AboutInfo {
  product_name: string
  product_version: string
  api_version: string
  schema_revision: string | null
  build_commit: string
  built_at: string
  environment: string
  runtime_profiles: string[]
  release_notes: AboutReleaseNotes | null
}

export const aboutApi = {
  get: () => api.get<AboutInfo>('/admin/system/about') as any,
}

// ============ 统计 API ============
const asCount = (value: unknown, fallback = 0) => {
  const count = Number(value)
  return Number.isFinite(count) ? count : fallback
}

export const statsApi = {
  getDashboardStats: async () => {
    const systemStats = await systemApi.stats()

    return {
      totalEvents: asCount(systemStats?.event_count),
      totalMemories: asCount(systemStats?.memory_count),
      todayMemories: asCount(systemStats?.today_memory_count),
      totalAgents: asCount(systemStats?.agent_count)
    }
  }
}

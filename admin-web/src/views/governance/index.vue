<template>
  <div class="page-container">
    <div class="page-header">
      <h2>记忆治理</h2>
    </div>

    <el-row :gutter="20">
      <el-col :span="8">
        <el-card class="stat-card">
          <div class="stat-content">
            <div class="stat-label">总记忆数</div>
            <div class="stat-value">{{ stats.totalMemories }}</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card class="stat-card">
          <div class="stat-content">
            <div class="stat-label">待治理</div>
            <div class="stat-value">{{ stats.pendingGovernance }}</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card class="stat-card">
          <div class="stat-content">
            <div class="stat-label">冲突记忆</div>
            <div class="stat-value">{{ stats.conflicts }}</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-card class="table-card" style="margin-top: 20px;">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="去重分析" name="dedup">
          <el-button type="primary" :loading="dedupLoading" @click="runDedup">运行去重分析</el-button>
          <el-table
            v-if="dedupResults.length"
            :data="dedupResults"
            stripe
            style="margin-top: 16px"
            v-loading="dedupLoading"
          >
            <el-table-column prop="memory_id_a" label="记忆 A" min-width="200" show-overflow-tooltip />
            <el-table-column prop="memory_id_b" label="记忆 B" min-width="200" show-overflow-tooltip />
            <el-table-column prop="similarity" label="相似度" width="120">
              <template #default="{ row }">
                <el-tag :type="(row.similarity || 0) >= 0.9 ? 'danger' : 'warning'">
                  {{ ((row.similarity || 0) * 100).toFixed(1) }}%
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="suggested_action" label="建议动作" width="150">
              <template #default="{ row }">
                <el-tag type="info">{{ getGovernanceActionLabel(row.suggested_action) }}</el-tag>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="未发现重复记忆" />
            </template>
          </el-table>
          <el-empty v-else-if="!dedupLoading && dedupRan" description="未发现重复记忆" />
        </el-tab-pane>

        <el-tab-pane label="冲突检测" name="conflict">
          <el-button type="primary" :loading="conflictLoading" @click="runConflictCheck">检测冲突</el-button>
          <el-table
            v-if="conflictResults.length"
            :data="conflictResults"
            stripe
            style="margin-top: 16px"
            v-loading="conflictLoading"
          >
            <el-table-column prop="conflict_type" label="类型" width="120" show-overflow-tooltip />
            <el-table-column prop="severity" label="严重度" width="100">
              <template #default="{ row }">
                <el-tag :type="getSeverityColor(row.severity)">{{ getSeverityLabel(row.severity) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="current_statement" label="当前陈述" min-width="200" show-overflow-tooltip />
            <el-table-column prop="past_statement" label="过往陈述" min-width="200" show-overflow-tooltip />
            <el-table-column prop="recommended_action" label="建议处理" width="150" show-overflow-tooltip>
              <template #default="{ row }">
                {{ getGovernanceActionLabel(row.recommended_action) }}
              </template>
            </el-table-column>
            <el-table-column prop="status" label="状态" width="100">
              <template #default="{ row }">
                {{ getGovernanceStatusLabel(row.status) }}
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="创建时间" width="180" />
            <template #empty>
              <el-empty description="未发现冲突记录" />
            </template>
          </el-table>
          <el-empty v-else-if="!conflictLoading && conflictRan" description="未发现冲突记录" />
        </el-tab-pane>

        <el-tab-pane label="记忆体检" name="hygiene">
          <div class="toolbar">
            <el-button type="primary" :loading="hygieneLoading" @click="runHygiene">运行体检</el-button>
            <el-button
              :disabled="!hasSelectedSupportedHygiene"
              :loading="hygieneApplying"
              @click="previewHygieneApply"
            >
              预览应用
            </el-button>
            <el-button
              type="danger"
              :disabled="!hasSelectedSupportedHygiene"
              :loading="hygieneApplying"
              @click="applySelectedHygiene"
            >
              应用已选建议
            </el-button>
          </div>

          <el-alert
            v-if="hygieneApplyResult"
            :type="hygieneApplyResult.errors?.length ? 'warning' : 'success'"
            :closable="false"
            title="体检应用结果"
            style="margin-top: 16px"
          >
            <div>
              转换 {{ hygieneApplyResult.proposals?.length || 0 }} 条，
              跳过 {{ hygieneApplyResult.unsupported?.length || 0 }} 条，
              已应用 {{ hygieneApplyResult.applied?.length || 0 }} 条。
            </div>
          </el-alert>

          <el-table
            v-if="hygieneSuggestions.length"
            :data="hygieneSuggestions"
            stripe
            style="margin-top: 16px"
            v-loading="hygieneLoading"
            @selection-change="handleHygieneSelectionChange"
          >
            <el-table-column type="selection" width="48" :selectable="isSelectableHygieneSuggestion" />
            <el-table-column prop="type" label="类型" width="180">
              <template #default="{ row }">
                <el-tag :type="isHygieneSuggestionSupported(row) ? 'success' : 'info'">
                  {{ getHygieneTypeLabel(row.type) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="priority" label="优先级" width="100">
              <template #default="{ row }">
                <el-tag :type="getPriorityColor(row.priority)">{{ getPriorityLabel(row.priority) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="confidence" label="置信度" width="110">
              <template #default="{ row }">
                {{ typeof row.confidence === 'number' ? `${(row.confidence * 100).toFixed(0)}%` : '-' }}
              </template>
            </el-table-column>
            <el-table-column label="关联记忆" min-width="220" show-overflow-tooltip>
              <template #default="{ row }">
                {{ formatHygieneMemoryIds(row) }}
              </template>
            </el-table-column>
            <el-table-column prop="reason" label="原因" min-width="260" show-overflow-tooltip />
            <el-table-column label="状态" width="110">
              <template #default="{ row }">
                <el-tag :type="isHygieneSuggestionSupported(row) ? 'success' : 'warning'">
                  {{ isHygieneSuggestionSupported(row) ? '可应用' : '仅提示' }}
                </el-tag>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="暂无体检建议" />
            </template>
          </el-table>
          <el-empty v-else-if="!hygieneLoading && hygieneRan" description="暂无体检建议" />
        </el-tab-pane>

        <el-tab-pane label="记忆合并" name="merge">
          <el-button type="primary" @click="openMergeDialog">合并记忆</el-button>
          <el-alert
            v-if="mergeResult"
            type="success"
            :closable="false"
            title="合并成功"
            style="margin-top: 16px"
          >
            合并后的记忆 ID：{{ mergeResult }}
          </el-alert>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <!-- 合并记忆对话框 -->
    <el-dialog v-model="mergeDialogVisible" title="合并记忆" width="500px">
      <el-form :model="mergeForm" label-width="100px">
        <el-form-item label="主记忆 ID" required>
          <el-input v-model="mergeForm.primary_id" placeholder="保留的记忆 ID" />
        </el-form-item>
        <el-form-item label="次记忆 ID" required>
          <el-input v-model="mergeForm.secondary_id" placeholder="被合并（标记为 SUPERSEDED）的记忆 ID" />
        </el-form-item>
        <div style="color: #909399; font-size: 13px; line-height: 1.6; padding-left: 100px;">
          合并后，次记忆会被标记为已替代（SUPERSEDED），其内容并入主记忆。
        </div>
      </el-form>
      <template #footer>
        <el-button @click="mergeDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="merging" @click="submitMerge">确定合并</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { governanceApi, memoriesApi } from '../../api'

type HygieneSuggestion = {
  type?: string
  memory_id?: string
  memory_ids?: string[]
  reason?: string
  confidence?: number
  priority?: string
  [key: string]: any
}

type HygieneApplyResult = {
  status?: string
  dry_run?: boolean
  proposals?: any[]
  unsupported?: any[]
  applied?: any[]
  errors?: any[]
}

const activeTab = ref('dedup')
const stats = ref({
  totalMemories: 0,
  pendingGovernance: 0,
  conflicts: 0
})

// 去重分析状态
const dedupLoading = ref(false)
const dedupRan = ref(false)
const dedupResults = ref<any[]>([])

// 冲突检测状态
const conflictLoading = ref(false)
const conflictRan = ref(false)
const conflictResults = ref<any[]>([])

// 记忆体检状态
const hygieneLoading = ref(false)
const hygieneApplying = ref(false)
const hygieneRan = ref(false)
const hygieneSuggestions = ref<HygieneSuggestion[]>([])
const selectedHygieneSuggestions = ref<HygieneSuggestion[]>([])
const hygieneApplyResult = ref<HygieneApplyResult | null>(null)

// 合并状态
const mergeDialogVisible = ref(false)
const merging = ref(false)
const mergeResult = ref('')
const mergeForm = ref({
  primary_id: '',
  secondary_id: ''
})

const getSeverityColor = (severity: string) => {
  const map: Record<string, string> = {
    'high': 'danger',
    'medium': 'warning',
    'low': 'info'
  }
  return map[severity] || 'info'
}

const getSeverityLabel = (severity?: string) => {
  const map: Record<string, string> = {
    high: '高',
    medium: '中',
    low: '低'
  }
  return map[severity || ''] || (severity || '-')
}

const getGovernanceActionLabel = (action?: string) => {
  const map: Record<string, string> = {
    merge: '合并',
    merge_a_into_b: '合并到记忆 B',
    merge_b_into_a: '合并到记忆 A',
    keep_both: '保留两条',
    reject: '拒绝',
    ignore: '忽略',
    review: '人工复核',
    update_current: '更新当前记忆',
    mark_outdated: '标记过期',
    supersede: '替代',
    no_action: '无需处理'
  }
  return map[action || ''] || (action || '-')
}

const getGovernanceStatusLabel = (status?: string) => {
  const map: Record<string, string> = {
    pending: '待处理',
    open: '待处理',
    resolved: '已解决',
    ignored: '已忽略',
    dismissed: '已忽略',
    merged: '已合并',
    failed: '失败'
  }
  return map[status || ''] || (status || '-')
}

const supportedHygieneTypes = new Set([
  'merge_duplicate_memories',
  'expire_or_rewrite_outdated_memory'
])

const hasSelectedSupportedHygiene = computed(() =>
  selectedHygieneSuggestions.value.some(isHygieneSuggestionSupported)
)

const isHygieneSuggestionSupported = (row: HygieneSuggestion) =>
  supportedHygieneTypes.has(row.type || '')

const isSelectableHygieneSuggestion = (row: HygieneSuggestion) =>
  isHygieneSuggestionSupported(row)

const getHygieneTypeLabel = (type?: string) => {
  const map: Record<string, string> = {
    merge_duplicate_memories: '重复合并',
    expire_or_rewrite_outdated_memory: '归档过期',
    low_confidence_memory: '低置信度',
    sparse_or_incomplete_memory: '信息不足'
  }
  return map[type || ''] || (type || '-')
}

const getPriorityColor = (priority?: string) => {
  const map: Record<string, string> = {
    high: 'danger',
    medium: 'warning',
    low: 'info'
  }
  return map[priority || ''] || 'info'
}

const getPriorityLabel = (priority?: string) => {
  const map: Record<string, string> = {
    high: '高',
    medium: '中',
    low: '低'
  }
  return map[priority || ''] || (priority || '-')
}

const formatHygieneMemoryIds = (row: HygieneSuggestion) => {
  const ids = Array.isArray(row.memory_ids)
    ? row.memory_ids
    : (row.memory_id ? [row.memory_id] : [])
  return ids.length ? ids.join(', ') : '-'
}

const handleHygieneSelectionChange = (rows: HygieneSuggestion[]) => {
  selectedHygieneSuggestions.value = rows
}

const runDedup = async () => {
  dedupLoading.value = true
  try {
    const res = await governanceApi.dedupAnalysis({
      similarity_threshold: 0.85,
      top_k: 50
    })
    dedupResults.value = res.pairs || []
    dedupRan.value = true
    if (dedupResults.value.length > 0) {
      ElMessage.success(`发现 ${dedupResults.value.length} 组重复记忆`)
    } else {
      ElMessage.info('未发现重复记忆')
    }
  } catch (error: any) {
    ElMessage.error('去重分析失败：' + (error?.message || '未知错误'))
  } finally {
    dedupLoading.value = false
  }
}

const runConflictCheck = async () => {
  conflictLoading.value = true
  try {
    const res = await governanceApi.conflictCheck({ limit: 50 })
    conflictResults.value = res.conflicts || []
    conflictRan.value = true
    if (conflictResults.value.length > 0) {
      ElMessage.success(`发现 ${conflictResults.value.length} 条冲突记录`)
      // 同步更新顶部统计
      stats.value.conflicts = conflictResults.value.length
    } else {
      ElMessage.info('未发现冲突记录')
    }
  } catch (error: any) {
    ElMessage.error('冲突检测失败：' + (error?.message || '未知错误'))
  } finally {
    conflictLoading.value = false
  }
}

const runHygiene = async (clearApplyResult = true) => {
  hygieneLoading.value = true
  if (clearApplyResult) {
    hygieneApplyResult.value = null
  }
  try {
    const res = await governanceApi.hygieneRun({
      dedup_threshold: 0.9,
      importance_floor: 0.4,
      max_pairs_per_user: 20
    })
    hygieneSuggestions.value = res.suggestions || []
    selectedHygieneSuggestions.value = []
    hygieneRan.value = true
    stats.value.pendingGovernance = hygieneSuggestions.value.length
    if (hygieneSuggestions.value.length > 0) {
      ElMessage.success(`发现 ${hygieneSuggestions.value.length} 条体检建议`)
    } else {
      ElMessage.info('暂无体检建议')
    }
  } catch (error: any) {
    ElMessage.error('记忆体检失败：' + (error?.message || '未知错误'))
  } finally {
    hygieneLoading.value = false
  }
}

const buildSelectedHygieneApplyPayload = (dryRun: boolean) => ({
  approved: true,
  dry_run: dryRun,
  suggestions: selectedHygieneSuggestions.value.filter(isHygieneSuggestionSupported)
})

const previewHygieneApply = async () => {
  if (!hasSelectedSupportedHygiene.value) {
    ElMessage.warning('请选择可应用的体检建议')
    return
  }
  hygieneApplying.value = true
  try {
    const res = await governanceApi.hygieneApply(buildSelectedHygieneApplyPayload(true))
    hygieneApplyResult.value = res
    ElMessage.success(`预览完成：${res.proposals?.length || 0} 条可转换`)
  } catch (error: any) {
    ElMessage.error('预览失败：' + (error?.message || '未知错误'))
  } finally {
    hygieneApplying.value = false
  }
}

const applySelectedHygiene = async () => {
  if (!hasSelectedSupportedHygiene.value) {
    ElMessage.warning('请选择可应用的体检建议')
    return
  }
  const selectedCount = selectedHygieneSuggestions.value.filter(isHygieneSuggestionSupported).length
  try {
    await ElMessageBox.confirm(
      `将应用 ${selectedCount} 条体检建议，可能合并或归档记忆。是否继续？`,
      '确认应用体检建议',
      { type: 'warning', confirmButtonText: '应用', cancelButtonText: '取消' }
    )
  } catch {
    return
  }

  hygieneApplying.value = true
  try {
    const res = await governanceApi.hygieneApply(buildSelectedHygieneApplyPayload(false))
    hygieneApplyResult.value = res
    ElMessage.success(`已应用 ${res.applied?.length || 0} 条体检建议`)
    await runHygiene(false)
  } catch (error: any) {
    ElMessage.error('应用失败：' + (error?.message || '未知错误'))
  } finally {
    hygieneApplying.value = false
  }
}

const openMergeDialog = () => {
  mergeForm.value = { primary_id: '', secondary_id: '' }
  mergeResult.value = ''
  mergeDialogVisible.value = true
}

const submitMerge = async () => {
  if (!mergeForm.value.primary_id.trim() || !mergeForm.value.secondary_id.trim()) {
    ElMessage.warning('请填写主记忆 ID 和次记忆 ID')
    return
  }
  if (mergeForm.value.primary_id.trim() === mergeForm.value.secondary_id.trim()) {
    ElMessage.warning('主记忆 ID 和次记忆 ID 不能相同')
    return
  }
  merging.value = true
  try {
    const res = await governanceApi.merge({
      primary_id: mergeForm.value.primary_id.trim(),
      secondary_id: mergeForm.value.secondary_id.trim()
    })
    mergeResult.value = res.merged_memory_id || res.merged_id || res.primary_id || ''
    ElMessage.success('合并成功')
    mergeDialogVisible.value = false
  } catch (error: any) {
    ElMessage.error('合并失败：' + (error?.message || '未知错误'))
  } finally {
    merging.value = false
  }
}

onMounted(async () => {
  try {
    const [memories, conflicts] = await Promise.all([
      memoriesApi.search({ query: '', top_k: 1 }),
      governanceApi.getConflicts({ limit: 1 })
    ])
    stats.value.totalMemories = memories.total || memories.count || 0
    stats.value.conflicts = conflicts.total || 0
  } catch (e) {
    console.error(e)
  }
})
</script>

<style scoped>
.page-container {
  padding: 20px;
  max-width: 1600px;
  position: relative;
}

/* 页面头部增强 */
.page-header {
  margin-bottom: 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid rgba(102, 126, 234, 0.1);
  position: relative;
}

.page-header::after {
  content: '';
  position: absolute;
  bottom: -1px;
  left: 0;
  width: 60px;
  height: 2px;
  background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
  border-radius: 2px;
}

.page-header h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: #1a1a2e;
}

/* 卡片增强 */
:deep(.el-card) {
  border-radius: 16px;
  border: none;
  background: rgba(255, 255, 255, 0.95);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  box-shadow:
    0 2px 8px rgba(0, 0, 0, 0.02),
    0 8px 24px rgba(0, 0, 0, 0.04),
    0 16px 48px rgba(0, 0, 0, 0.03);
  transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
  overflow: hidden;
}

:deep(.el-card::before) {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
  opacity: 0;
  transition: opacity 0.3s ease;
}

:deep(.el-card:hover) {
  transform: translateY(-4px);
  box-shadow:
    0 4px 12px rgba(0, 0, 0, 0.04),
    0 12px 32px rgba(0, 0, 0, 0.06),
    0 24px 64px rgba(0, 0, 0, 0.04),
    0 0 30px rgba(102, 126, 234, 0.1);
}

:deep(.el-card:hover::before) {
  opacity: 1;
}

/* 统计卡片增强 */
.stat-card {
  text-align: center;
}

.stat-card :deep(.el-card) {
  border-radius: 16px;
}

.stat-content {
  padding: 24px;
}

.stat-label {
  font-size: 14px;
  color: #909399;
  margin-bottom: 12px;
  font-weight: 500;
}

.stat-value {
  font-size: 36px;
  font-weight: 700;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.table-card {
  margin-top: 24px;
}

.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
}

/* 按钮增强 */
:deep(.el-button--primary) {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border: none;
  border-radius: 10px;
  font-weight: 500;
  transition: all 0.3s ease;
  box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3);
}

:deep(.el-button--primary:hover) {
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(102, 126, 234, 0.4);
}

/* Tabs 增强 */
:deep(.el-tabs__item) {
  font-weight: 500;
  transition: all 0.3s ease;
}

:deep(.el-tabs__item:hover) {
  color: #667eea;
}

:deep(.el-tabs__item.is-active) {
  color: #667eea;
}

:deep(.el-tabs__active-bar) {
  background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
}

/* 页面进入动画 */
@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.page-container {
  animation: fadeInUp 0.5s ease-out;
}
</style>

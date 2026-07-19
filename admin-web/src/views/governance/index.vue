<template>
  <section class="governance-page">
    <LmPageHeader title="记忆治理审计" subtitle="工作 Agent 自动治理正式记忆；所有变更保留来源、决策轨迹和回滚窗口。" />

    <el-alert
      title="这里是只读分析与审计入口，不再提供候选审核、人工体检应用或手工合并。"
      type="info"
      :closable="false"
      show-icon
      class="governance-alert"
    />

    <el-tabs v-model="activeTab" class="governance-tabs">
      <el-tab-pane label="冲突记录" name="conflicts">
        <div class="toolbar">
          <el-button :loading="loadingConflicts" @click="loadConflicts">刷新</el-button>
        </div>
        <el-table :data="conflicts" v-loading="loadingConflicts" row-key="id" empty-text="暂无冲突记录">
          <el-table-column prop="conflict_type" label="类型" min-width="150" />
          <el-table-column prop="current_statement" label="当前陈述" min-width="220" show-overflow-tooltip />
          <el-table-column prop="past_statement" label="历史陈述" min-width="220" show-overflow-tooltip />
          <el-table-column prop="severity" label="级别" width="100" />
          <el-table-column prop="status" label="状态" width="120" />
          <el-table-column prop="created_at" label="发现时间" min-width="170">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="有界去重分析" name="dedup">
        <div class="toolbar">
          <el-button type="primary" :loading="loadingDedup" @click="runDedup">运行只读分析</el-button>
          <span>每条近期记忆最多比较 20 个同分区近邻，不执行全库两两扫描。</span>
        </div>
        <el-table :data="duplicatePairs" v-loading="loadingDedup" row-key="memory_id_a" empty-text="暂无高相似记忆">
          <el-table-column prop="memory_id_a" label="记忆 A" min-width="190" />
          <el-table-column prop="memory_id_b" label="记忆 B" min-width="190" />
          <el-table-column prop="similarity" label="相似度" width="110" />
          <el-table-column prop="suggested_action" label="建议" width="140" />
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="维护动作" name="actions">
        <div class="toolbar">
          <el-button :loading="loadingActions" @click="loadActions">刷新</el-button>
          <span>回滚请在 Agent Runtime 页面执行，避免把审计页变成第二写入口。</span>
        </div>
        <el-table :data="actions" v-loading="loadingActions" row-key="id" empty-text="暂无维护动作">
          <el-table-column prop="action" label="动作" width="120" />
          <el-table-column prop="state" label="状态" width="120" />
          <el-table-column prop="reason_code" label="依据" min-width="220" />
          <el-table-column prop="input_memory_ids" label="记忆数量" width="110">
            <template #default="{ row }">{{ row.input_memory_ids?.length || 0 }}</template>
          </el-table-column>
          <el-table-column prop="reversible_until" label="可回滚至" min-width="180">
            <template #default="{ row }">{{ formatTime(row.reversible_until) }}</template>
          </el-table-column>
          <el-table-column prop="created_at" label="执行时间" min-width="180">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { governanceApi, runtimeApi } from '../../api'
import LmPageHeader from '../../components/LmPageHeader.vue'

const activeTab = ref('conflicts')
const conflicts = ref<any[]>([])
const duplicatePairs = ref<any[]>([])
const actions = ref<any[]>([])
const loadingConflicts = ref(false)
const loadingDedup = ref(false)
const loadingActions = ref(false)

const formatTime = (value?: string | null) => value ? new Date(value).toLocaleString('zh-CN') : '—'

const loadConflicts = async () => {
  loadingConflicts.value = true
  try {
    const response = await governanceApi.getConflicts()
    conflicts.value = Array.isArray(response) ? response : response?.items || []
  } finally {
    loadingConflicts.value = false
  }
}

const runDedup = async () => {
  loadingDedup.value = true
  try {
    const response = await governanceApi.dedupAnalysis({ similarity_threshold: 0.9, top_k: 20 })
    duplicatePairs.value = response?.pairs || []
    ElMessage.success(`分析完成，发现 ${duplicatePairs.value.length} 组近邻`)
  } finally {
    loadingDedup.value = false
  }
}

const loadActions = async () => {
  loadingActions.value = true
  try {
    const response = await runtimeApi.maintenanceActions(100)
    actions.value = response?.items || []
  } finally {
    loadingActions.value = false
  }
}

onMounted(() => Promise.all([loadConflicts(), loadActions()]))
</script>

<style scoped>
.governance-page { max-width: 1180px; margin: 0 auto; }
.governance-alert { margin-bottom: 18px; }
.governance-tabs { padding: 18px; border: 1px solid var(--lm-color-border); border-radius: var(--lm-radius-md); background: var(--lm-color-bg-card); }
.toolbar { display: flex; align-items: center; gap: 14px; margin-bottom: 14px; color: var(--lm-color-text-secondary); font-size: 13px; }
@media (max-width: 767px) { .toolbar { align-items: flex-start; flex-direction: column; } }
</style>

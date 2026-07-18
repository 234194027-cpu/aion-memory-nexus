<template>
  <div class="page-container">
    <div class="page-header">
      <h2>人物画像</h2>
      <el-button type="primary" @click="rebuildPersona" :loading="rebuilding">
        <el-icon><Plus /></el-icon>重新生成画像
      </el-button>
    </div>

    <el-card class="table-card">
      <el-skeleton v-if="loading" :rows="6" animated />
      <template v-else>
        <LmEmptyState
          v-if="!persona"
          description="暂无人物画像，请先积累记忆或点击重新生成画像。"
          action-text="重新生成画像"
          :action-icon="Plus"
          @action="rebuildPersona"
        />
        <div v-else class="persona-summary">
          <el-descriptions :column="1" border>
            <el-descriptions-item label="摘要">{{ persona.summary || '-' }}</el-descriptions-item>
            <el-descriptions-item label="证据数量">{{ persona.evidence_count || 0 }}</el-descriptions-item>
            <el-descriptions-item label="快照日期">{{ persona.snapshot_date || '-' }}</el-descriptions-item>
            <el-descriptions-item label="生成时间">{{ formatTime(persona.generated_at) }}</el-descriptions-item>
          </el-descriptions>

          <el-card class="traits-card">
            <template #header>
              <div class="card-header">
                <span>核心特征</span>
              </div>
            </template>
            <pre class="traits-text">{{ formatTraits(persona.traits) }}</pre>
          </el-card>
        </div>
      </template>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { personaApi } from '../../api'
import LmEmptyState from '../../components/LmEmptyState.vue'

const loading = ref(false)
const rebuilding = ref(false)
const persona = ref<any>(null)

const messageFromError = (e: any, fallback: string) => {
  return e?.response?.data?.detail || e?.message || fallback
}

const isMissingPersona = (e: any) => {
  return e?.response?.status === 404
}

const hasPersonaSnapshot = (data: any) => {
  return Boolean(data?.snapshot_id || data?.summary || data?.evidence_count)
}

const formatTime = (value?: string) => {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN')
}

const fetchData = async () => {
  loading.value = true
  try {
    const data = await personaApi.list()
    persona.value = hasPersonaSnapshot(data) ? data : null
  } catch (e: any) {
    persona.value = null
    if (!isMissingPersona(e)) {
      ElMessage.error(messageFromError(e, '获取人物画像失败'))
    }
  } finally {
    loading.value = false
  }
}

const rebuildPersona = async () => {
  rebuilding.value = true
  try {
    const data = await personaApi.rebuild({})
    persona.value = hasPersonaSnapshot(data) ? data : null
    if (persona.value) {
      ElMessage.success('人物画像已重新生成')
    } else {
      ElMessage.info(data?.summary || '暂无足够记忆生成画像')
    }
  } catch (e: any) {
    ElMessage.error(messageFromError(e, '重建失败'))
  } finally {
    rebuilding.value = false
  }
}

const formatTraits = (traits: any) => {
  if (!traits) return '暂无核心特征'
  return typeof traits === 'string' ? traits : JSON.stringify(traits, null, 2)
}

onMounted(() => {
  fetchData()
})
</script>

<style scoped>
.page-container {
  padding: 20px;
  max-width: 1600px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid #e5e7eb;
}

.page-header h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: #111827;
}

.table-card,
.traits-card {
  border-radius: 8px;
}

.traits-card {
  margin-top: 20px;
}

.traits-text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  color: #374151;
  line-height: 1.6;
}

.card-header {
  font-weight: 600;
}
</style>

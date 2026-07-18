<template>
  <div class="page-container">
    <div class="page-header">
      <h2>每日简报</h2>
      <el-button type="primary" :loading="loading" @click="refreshBriefing">
        <el-icon><Refresh /></el-icon>刷新简报
      </el-button>
    </div>

    <el-card v-if="todayReport">
      <template #header>
        <div class="card-header">
          <span>{{ briefingTimestamp }}</span>
        </div>
      </template>
      <div class="report-content">
        <p class="headline">{{ todayReport.headline }}</p>
        <p v-if="todayReport.suggested_next_step">{{ todayReport.suggested_next_step }}</p>
        <p v-if="todayReport.open_decision"><strong>待推进决策：</strong>{{ todayReport.open_decision.title }}</p>
        <p v-if="todayReport.old_conflict"><strong>待处理冲突：</strong>{{ todayReport.old_conflict.interpretation }}</p>
        <p v-if="todayReport.echo_principle"><strong>回顾原则：</strong>{{ todayReport.echo_principle.title }}：{{ todayReport.echo_principle.body }}</p>
      </div>
    </el-card>

    <el-card class="open-loops-card">
      <template #header>
        <div class="card-header"><span>开放事项</span><span class="count">{{ openLoops.length }}</span></div>
      </template>
      <div v-if="openLoops.length" class="open-loops-list">
        <div v-for="item in openLoops" :key="`${item.source_type}-${item.source_id}`" class="open-loop-item">
          <el-tag size="small" effect="plain">{{ sourceLabel(item.source_type) }}</el-tag>
          <div class="open-loop-copy"><strong>{{ item.title }}</strong><span>{{ item.next_step }}</span></div>
        </div>
      </div>
      <LmEmptyState v-else description="今天没有需要跟进的开放事项" :image-size="48" />
    </el-card>

    <LmEmptyState
      v-if="!todayReport"
      description="暂无今日简报"
      action-text="刷新简报"
      :action-icon="Refresh"
      @action="refreshBriefing"
    />

  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { dailyApi, runtimeApi, type OpenLoopItem } from '../../api'
import LmEmptyState from '../../components/LmEmptyState.vue'

const loading = ref(false)
const todayReport = ref<any>(null)
const openLoops = ref<OpenLoopItem[]>([])

const briefingTimestamp = computed(() => {
  const value = todayReport.value?.generated_at
  return value ? new Date(value).toLocaleString() : '今日简报'
})

const fetchData = async () => {
  loading.value = true
  try {
    const [briefing, loops] = await Promise.all([dailyApi.getBriefing(), runtimeApi.openLoops()])
    todayReport.value = briefing
    openLoops.value = loops.items || []
  } catch (e: any) {
    ElMessage.error(e.message || '获取数据失败')
  } finally {
    loading.value = false
  }
}

const sourceLabel = (source: string) => ({ handoff: '待补证据', task: '待办', conflict: '冲突', decision: '决策' }[source] || '事项')

const refreshBriefing = async () => {
  try {
    await fetchData()
    ElMessage.success('简报已刷新')
  } catch (e: any) {
    ElMessage.error(e.message || '生成失败')
  }
}

onMounted(() => {
  fetchData()
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
  display: flex;
  justify-content: space-between;
  align-items: center;
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

.card-header {
  font-weight: 600;
  color: #1a1a2e;
  position: relative;
  padding-bottom: 12px;
  border-bottom: 1px solid rgba(102, 126, 234, 0.1);
}

.card-header::after {
  content: '';
  position: absolute;
  bottom: -1px;
  left: 0;
  width: 40px;
  height: 2px;
  background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
  border-radius: 2px;
}

.report-content {
  line-height: 1.8;
  color: #303133;
}

.headline {
  font-size: 18px;
  font-weight: 600;
}

.open-loops-card { margin-top: 18px; }
.count { color: #a35d39; font-variant-numeric: tabular-nums; }
.open-loops-list { display: grid; gap: 10px; }
.open-loop-item { display: flex; align-items: flex-start; gap: 10px; padding: 10px 0; border-bottom: 1px solid #eee7dd; }
.open-loop-item:last-child { border-bottom: 0; }
.open-loop-copy { display: grid; gap: 3px; line-height: 1.45; }
.open-loop-copy span { color: #68706d; font-size: 13px; }

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

/* 表格增强 */
:deep(.el-table) {
  --el-table-border-color: rgba(102, 126, 234, 0.1);
  --el-table-header-bg-color: rgba(102, 126, 234, 0.03);
  border-radius: 12px;
  overflow: hidden;
}

:deep(.el-table th.el-table__cell) {
  background: linear-gradient(135deg, rgba(102, 126, 234, 0.08) 0%, rgba(118, 75, 162, 0.08) 100%);
  font-weight: 600;
  color: #1a1a2e;
}

:deep(.el-table tr) {
  transition: all 0.3s ease;
}

:deep(.el-table tr:hover > td.el-table__cell) {
  background: linear-gradient(135deg, rgba(102, 126, 234, 0.06) 0%, rgba(118, 75, 162, 0.04) 100%) !important;
}

:deep(.el-table--striped .el-table__body tr.el-table__row--striped td.el-table__cell) {
  background: rgba(102, 126, 234, 0.02);
}

/* 链接按钮 */
:deep(.el-button--primary.is-link) {
  background: transparent;
  border: none;
  color: #667eea;
  font-weight: 500;
}

:deep(.el-button--primary.is-link:hover) {
  color: #764ba2;
  background: rgba(102, 126, 234, 0.1);
  transform: translateX(2px);
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

@media (prefers-reduced-motion: reduce) {
  .page-container,
  :deep(.el-card),
  :deep(.el-button--primary),
  :deep(.el-table tr) {
    animation: none;
    transition: none;
  }
}
</style>

<template>
  <div class="page-container">
    <div class="page-header">
      <h2>Obsidian 同步</h2>
      <el-button type="primary" @click="handleSync">
        <el-icon><Refresh /></el-icon>立即同步
      </el-button>
    </div>

    <el-card v-if="status">
      <el-descriptions :column="2" border>
        <el-descriptions-item label="连接状态">
          <el-tag :type="status.connected ? 'success' : 'danger'">
            {{ status.connected ? '已连接' : '未连接' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="最后同步">
          {{ status.lastSync || '从未同步' }}
        </el-descriptions-item>
        <el-descriptions-item label="保险库数量">
          {{ status.vaultCount || 0 }}
        </el-descriptions-item>
        <el-descriptions-item label="同步状态">
          {{ syncStatusLabel(status.syncStatus) }}
        </el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-card class="vault-card" style="margin-top: 20px;">
      <template #header>
        <div class="card-header">
          <span>保险库列表</span>
          <el-button type="primary" link @click="fetchStatus">刷新</el-button>
        </div>
      </template>
      <el-table :data="vaults" v-loading="loading">
        <el-table-column prop="name" label="名称" />
        <el-table-column prop="path" label="路径" show-overflow-tooltip />
        <el-table-column prop="lastModified" label="最后修改" width="180" />
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link @click="syncVault(row)">同步</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { obsidianApi } from '../../api'

const loading = ref(false)
const status = ref<any>(null)
const vaults = ref<any[]>([])

const syncStatusLabel = (value?: string) => {
  const map: Record<string, string> = {
    idle: '空闲',
    pending: '等待同步',
    syncing: '同步中',
    running: '同步中',
    success: '同步成功',
    completed: '已完成',
    failed: '同步失败',
    error: '同步失败'
  }
  return value ? map[value] || value : '未知'
}

const fetchStatus = async () => {
  loading.value = true
  try {
    const [statusData, vaultsData] = await Promise.all([
      obsidianApi.status(),
      obsidianApi.getVaults()
    ])
    status.value = statusData
    vaults.value = vaultsData.vaults || []
  } catch (e: any) {
    ElMessage.error(e.message || '获取状态失败')
  } finally {
    loading.value = false
  }
}

const handleSync = async () => {
  try {
    await obsidianApi.sync({})
    ElMessage.success('同步已开始')
    setTimeout(fetchStatus, 2000)
  } catch (e: any) {
    ElMessage.error(e.message || '同步失败')
  }
}

const syncVault = async (vault: any) => {
  try {
    await obsidianApi.sync({ vault: vault.name })
    ElMessage.success('保险库同步已开始')
  } catch (e: any) {
    ElMessage.error(e.message || '同步失败')
  }
}

onMounted(() => {
  fetchStatus()
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
  display: flex;
  justify-content: space-between;
  align-items: center;
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

.vault-card {
  margin-top: 24px;
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

:deep(.el-button--primary.is-link) {
  background: transparent;
  border: none;
  color: #667eea;
  font-weight: 500;
}

:deep(.el-button--primary.is-link:hover) {
  color: #764ba2;
  background: rgba(102, 126, 234, 0.1);
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

/* 标签增强 */
:deep(.el-tag) {
  border: none;
  font-weight: 500;
  border-radius: 6px;
  transition: all 0.3s ease;
}

:deep(.el-tag:hover) {
  transform: scale(1.05);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

/* 描述列表增强 */
:deep(.el-descriptions) {
  border-radius: 12px;
  overflow: hidden;
}

:deep(.el-descriptions__label) {
  background: rgba(102, 126, 234, 0.05);
  font-weight: 500;
  color: #1a1a2e;
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

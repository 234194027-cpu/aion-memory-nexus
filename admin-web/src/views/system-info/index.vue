<!--
  @deprecated WP-10: 此页面已被 /about（关于与版本页面）取代。
  路由 /system-info 已重定向到 /about（见 router/index.ts）。
  本文件保留仅为历史参考，不再维护。新功能请添加到 views/about/index.vue。
-->
<template>
  <div class="page-container">
    <el-alert
      type="warning"
      :closable="false"
      show-icon
      style="margin-bottom: 16px;"
    >
      此页面已弃用，已迁移到「关于与版本」页面。正在跳转…
    </el-alert>
    <div class="page-header">
      <h2>系统信息</h2>
      <el-button @click="fetchData">
        <el-icon><Refresh /></el-icon>刷新
      </el-button>
    </div>

    <el-row :gutter="20">
      <el-col :span="12">
        <el-card class="info-card">
          <template #header>
            <div class="card-header">
              <span>系统状态</span>
            </div>
          </template>
          <el-descriptions :column="1" border v-if="health">
            <el-descriptions-item label="状态">
              <el-tag :type="health.status === 'healthy' ? 'success' : 'danger'">
                {{ health.status === 'healthy' ? '健康' : '异常' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="环境">
              {{ systemInfo?.environment || 'unknown' }}
            </el-descriptions-item>
            <el-descriptions-item label="数据库">
              {{ health.database || systemInfo?.database }}
            </el-descriptions-item>
            <el-descriptions-item label="版本">
              {{ systemInfo?.version }}
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>

      <el-col :span="12">
        <el-card class="info-card">
          <template #header>
            <div class="card-header">
              <span>运行统计</span>
            </div>
          </template>
          <el-descriptions :column="1" border v-if="stats">
            <el-descriptions-item label="总事件数">
              {{ stats.event_count }}
            </el-descriptions-item>
            <el-descriptions-item label="总记忆数">
              {{ stats.memory_count }}
            </el-descriptions-item>
            <el-descriptions-item label="今日新增记忆">
              {{ stats.today_memory_count }}
            </el-descriptions-item>
            <el-descriptions-item label="Agent 数量">
              {{ stats.agent_count }}
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
    </el-row>

    <el-card class="info-card" style="margin-top: 20px;">
      <template #header>
        <div class="card-header">
          <span>健康检查</span>
        </div>
      </template>
      <el-result
        v-if="health"
        :icon="health.status === 'healthy' ? 'success' : 'warning'"
        :title="health.status === 'healthy' ? '系统运行正常' : '系统存在异常'"
        :sub-title="health.message || '所有服务正常运行'"
      >
        <template #extra>
          <el-button type="primary" @click="fetchData">重新检查</el-button>
        </template>
      </el-result>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { systemApi } from '../../api'

const loading = ref(false)
const systemInfo = ref<any>(null)
const stats = ref<any>(null)
const health = ref<any>(null)

const fetchData = async () => {
  loading.value = true
  try {
    const [info, statsData, healthData] = await Promise.all([
      systemApi.info(),
      systemApi.stats(),
      systemApi.health()
    ])
    systemInfo.value = info
    stats.value = statsData
    health.value = healthData
  } catch (e: any) {
    ElMessage.error(e.message || '获取信息失败')
  } finally {
    loading.value = false
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

.info-card {
  margin-bottom: 24px;
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

/* Result 组件增强 */
:deep(.el-result) {
  padding: 32px 0;
}

:deep(.el-result__icon svg) {
  transition: all 0.3s ease;
}

:deep(.el-result__icon svg:hover) {
  transform: scale(1.1);
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

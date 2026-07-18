<template>
  <div class="page-container">
    <div class="page-header">
      <h2>企业微信</h2>
    </div>

    <el-card v-if="config">
      <el-descriptions :column="2" border>
        <el-descriptions-item label="连接状态">
          <el-tag :type="isConnected ? 'success' : 'danger'">
            {{ isConnected ? '已连接' : '未连接' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="Bot ID">
          {{ config.bot_id || '-' }}
        </el-descriptions-item>
        <el-descriptions-item label="Agent ID">
          {{ config.default_agent_id || '-' }}
        </el-descriptions-item>
        <el-descriptions-item label="部署配置">
          {{ config.enabled ? '已配置' : '未配置' }}
        </el-descriptions-item>
      </el-descriptions>

      <p v-if="!config.enabled" class="config-hint">企业微信凭据由部署环境配置；此页面不会保存或传输 Secret。</p>
      <div class="action-buttons" v-if="!isConnected">
        <el-button type="primary" :disabled="!config.enabled" @click="handleConnect">连接企业微信</el-button>
      </div>
      <div class="action-buttons" v-else>
        <el-button type="primary" @click="testMessage">发送测试消息</el-button>
        <el-button type="danger" @click="handleDisconnect">断开连接</el-button>
      </div>
    </el-card>

    <el-card v-if="conversationState" class="profile-card">
      <template #header>
        <div class="profile-header">
          <div>
            <span>对话 Agent 状态</span>
            <small>最近反思：{{ conversationState.last_reflected_at || '尚未生成' }}</small>
          </div>
          <el-button type="primary" :loading="heartbeatLoading" @click="runHeartbeat">运行一次 Heartbeat</el-button>
        </div>
      </template>
      <div class="active-question">
        <el-tag type="success">最近摘要</el-tag>
        <span>{{ conversationState.summary || '暂无已反思的对话片段' }}</span>
      </div>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="今日主动触达">{{ conversationState.proactive_sent_today }} / {{ conversationState.proactive_daily_limit }}</el-descriptions-item>
        <el-descriptions-item label="剩余额度">{{ conversationState.proactive_remaining_today }}</el-descriptions-item>
      </el-descriptions>
      <div class="domain-grid">
        <div v-for="(item, index) in conversationState.open_items" :key="index" class="domain-item">
          <span>{{ item.text || item.title || '开放事项' }}</span>
          <el-tag size="small" type="warning">开放</el-tag>
        </div>
      </div>
    </el-card>

    <el-empty v-else description="加载中..." />

  </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { runtimeApi, wecomApi } from '../../api'

const loading = ref(false)
const config = ref<any>(null)
const conversationState = ref<any>(null)
const heartbeatLoading = ref(false)
const isConnected = computed(() => Boolean(config.value?.bot_status?.connected))

const fetchData = async () => {
  loading.value = true
  try {
    const [configData, stateData] = await Promise.all([wecomApi.getConfig(), runtimeApi.conversationState()])
    config.value = configData
    conversationState.value = stateData
  } catch (e: any) {
    ElMessage.error(e.message || '获取配置失败')
  } finally {
    loading.value = false
  }
}

const handleConnect = async () => {
  try {
    const result = await wecomApi.connect()
    ElMessage.success(result?.status === 'already_connected' ? '企业微信已连接' : '正在建立连接')
    await fetchData()
  } catch (e: any) {
    ElMessage.error(e.message || '连接失败')
  }
}

const handleDisconnect = async () => {
  try {
    await ElMessageBox.confirm('确定要断开企业微信连接吗?', '提示', {
      type: 'warning'
    })
    await wecomApi.disconnect()
    ElMessage.success('已断开连接')
    fetchData()
  } catch (e: any) {
    if (e !== 'cancel') {
      ElMessage.error(e.message || '操作失败')
    }
  }
}

const testMessage = async () => {
  try {
    const { value: userId } = await ElMessageBox.prompt('输入接收测试消息的企业微信用户 ID。', '发送测试消息', {
      inputPattern: /\S+/,
      inputErrorMessage: '用户 ID 不能为空',
      confirmButtonText: '发送',
      cancelButtonText: '取消',
    })
    await wecomApi.testMessage({ user_id: userId, content: '测试消息' })
    ElMessage.success('测试消息已发送')
  } catch (e: any) {
    ElMessage.error(e.message || '发送失败')
  }
}

const runHeartbeat = async () => {
  heartbeatLoading.value = true
  try {
    const response = await wecomApi.runConversationHeartbeat()
    ElMessage.success(`Heartbeat：${response?.result?.status || 'completed'}`)
    await fetchData()
  } catch (e: any) {
    ElMessage.error(e.message || 'Heartbeat 运行失败')
  } finally {
    heartbeatLoading.value = false
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

:deep(.el-button--danger) {
  background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%);
  border: none;
  border-radius: 10px;
  transition: all 0.3s ease;
  box-shadow: 0 4px 12px rgba(239, 68, 68, 0.3);
}

:deep(.el-button--danger:hover) {
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(239, 68, 68, 0.4);
}

.action-buttons {
  margin-top: 24px;
  display: flex;
  gap: 12px;
}

.config-hint {
  margin: 16px 0 0;
  color: #7a8190;
  font-size: 13px;
}

.profile-card { margin-top: 20px; }
.profile-header, .feedback-actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.profile-header small { display: block; color: #7a8190; margin-top: 4px; }
.active-question { margin-bottom: 18px; display: grid; gap: 10px; }
.feedback-actions { justify-content: flex-start; flex-wrap: wrap; }
.domain-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 8px; margin-top: 18px; }
.domain-item { display: flex; justify-content: space-between; gap: 8px; padding: 10px 12px; border-radius: 10px; background: #f7f8fb; }
.state-answered { background: #edf9f1; }
.state-declined { opacity: .58; }

/* 对话框增强 */
:deep(.el-dialog) {
  border-radius: 16px;
  overflow: hidden;
}

:deep(.el-dialog__header) {
  background: linear-gradient(135deg, rgba(102, 126, 234, 0.05) 0%, rgba(118, 75, 162, 0.05) 100%);
  border-bottom: 1px solid rgba(102, 126, 234, 0.1);
  padding: 20px 24px;
}

:deep(.el-dialog__title) {
  font-weight: 600;
  color: #1a1a2e;
}

:deep(.el-form-item__label) {
  font-weight: 500;
  color: #1a1a2e;
}

:deep(.el-input__wrapper) {
  border-radius: 10px;
  box-shadow: 0 0 0 1px rgba(102, 126, 234, 0.2) inset;
  transition: all 0.3s ease;
}

:deep(.el-input__wrapper:hover) {
  box-shadow: 0 0 0 1px rgba(102, 126, 234, 0.4) inset;
}

:deep(.el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 1px #667eea inset, 0 0 0 3px rgba(102, 126, 234, 0.15);
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
  :deep(.el-tag),
  :deep(.el-button--primary),
  :deep(.el-button--danger) {
    animation: none;
    transition: none;
  }
}
</style>

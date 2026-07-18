<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <p class="eyebrow">PERSONAL MEMORY</p>
        <h2>对话</h2>
      </div>
      <el-tag :type="runtimeReady ? 'success' : 'info'" effect="plain">
        {{ runtimeReady ? '对话 Agent 已启用' : '兼容顾问模式' }}
      </el-tag>
    </div>

    <el-card class="chat-card">
      <div class="chat-messages" ref="messagesContainer">
        <div
          v-for="(msg, index) in messages"
          :key="index"
          :class="['message', msg.role]"
        >
          <div class="message-content">
            <div>{{ msg.content }}</div>
            <div v-if="msg.citations?.length" class="citations">依据：{{ formatCitations(msg) }}</div>
            <div v-if="msg.meta" class="message-meta">{{ msg.meta }}</div>
          </div>
        </div>
      </div>

      <div class="chat-input">
        <el-input
          v-model="inputMessage"
          placeholder="问问过去的决定、计划，或继续一段思考…"
          @keyup.enter="handleSend"
          :disabled="loading"
        >
          <template #append>
            <el-button @click="handleSend" :loading="loading">发送</el-button>
          </template>
        </el-input>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { advisorApi, runtimeApi } from '../../api'

const loading = ref(false)
const inputMessage = ref('')
const runtimeReady = ref(false)
type CitationEvidence = {
  memory_id: string
  source_event_ids: string[]
  epistemic_status: string
  valid_from: string | null
  valid_until: string | null
}

const messages = ref<{ role: string; content: string; citations?: string[]; citationEvidence?: CitationEvidence[]; meta?: string }[]>([
  { role: 'assistant', content: '我会在需要时检索你的记忆，并说明依据；没有足够证据时会直接告诉你。' }
])

const sessionKey = (() => {
  const key = 'life-memory-conversation-session'
  const existing = sessionStorage.getItem(key)
  if (existing) return existing
  const next = `web-${crypto.randomUUID()}`
  sessionStorage.setItem(key, next)
  return next
})()

onMounted(async () => {
  try {
    const status = await runtimeApi.status()
    runtimeReady.value = Boolean(status.runtime_enabled && status.conversational_enabled)
  } catch {
    runtimeReady.value = false
  }
})

const handleSend = async () => {
  if (!inputMessage.value.trim()) return

  const userMessage = inputMessage.value.trim()
  messages.value.push({ role: 'user', content: userMessage })
  inputMessage.value = ''
  loading.value = true

  try {
    if (runtimeReady.value) {
      const res = await runtimeApi.converse({ message: userMessage, session_key: sessionKey })
      messages.value.push({
        role: 'assistant',
        content: res.text,
        citations: res.citations,
        citationEvidence: res.citation_evidence,
        meta: `${res.response_mode} · ${res.confidence} · Run ${res.run_id}`
      })
    } else {
      const res = await advisorApi.ask({ question: userMessage })
      messages.value.push({ role: 'assistant', content: res.answer || res.message || '收到回答', meta: '兼容顾问模式' })
    }
  } catch (e: any) {
    ElMessage.error(e.message || '请求失败')
  } finally {
    loading.value = false
  }
}

function formatCitations(message: { citations?: string[]; citationEvidence?: CitationEvidence[] }) {
  if (!message.citationEvidence?.length) return message.citations?.join(' · ') || ''
  return message.citationEvidence.map((item) => {
    const start = item.valid_from?.slice(0, 10) || '未知'
    const end = item.valid_until?.slice(0, 10) || '至今'
    return `${item.memory_id}（来源 ${item.source_event_ids.length} 条；${item.epistemic_status}；有效 ${start} 至 ${end}）`
  }).join('；')
}
</script>

<style scoped>
.page-container {
  padding: 20px;
  height: calc(100vh - 120px);
  display: flex;
  flex-direction: column;
  max-width: 1600px;
  position: relative;
}

/* 页面头部增强 */
.page-header {
  margin-bottom: 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid rgba(102, 126, 234, 0.1);
  position: relative;
  display: flex;
  align-items: end;
  justify-content: space-between;
}

.page-header::after {
  content: '';
  position: absolute;
  bottom: -1px;
  left: 0;
  width: 60px;
  height: 2px;
  background: #d77546;
  border-radius: 2px;
}

.page-header h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: #263238;
}

.eyebrow { margin: 0 0 4px; color: #a35d39; font-size: 11px; font-weight: 700; letter-spacing: .12em; }

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

.chat-card {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

/* 消息气泡增强 */
.message {
  margin-bottom: 20px;
  display: flex;
  animation: fadeInUp 0.3s ease-out;
}

.message.user {
  justify-content: flex-end;
}

.message-content {
  max-width: 70%;
  padding: 14px 20px;
  border-radius: 18px;
  line-height: 1.6;
  transition: all 0.3s ease;
}

.message.user .message-content {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: #fff;
  border-bottom-right-radius: 4px;
  box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3);
}

.message.assistant .message-content {
  background: #f5f0e8;
  color: #263238;
  border-bottom-left-radius: 4px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
}

.citations, .message-meta { margin-top: 9px; font-size: 12px; line-height: 1.4; }
.citations { color: #8a5a3d; }
.message-meta { color: #7a8380; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }

.message.assistant:hover .message-content {
  transform: translateX(4px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.06);
}

.chat-input {
  padding: 20px 24px;
  border-top: 1px solid rgba(102, 126, 234, 0.1);
  background: linear-gradient(135deg, rgba(102, 126, 234, 0.02) 0%, rgba(118, 75, 162, 0.02) 100%);
}

/* 输入框增强 */
:deep(.el-input__wrapper) {
  border-radius: 24px;
  box-shadow: 0 0 0 1px rgba(102, 126, 234, 0.2) inset;
  transition: all 0.3s ease;
  padding: 8px 16px;
}

:deep(.el-input__wrapper:hover) {
  box-shadow: 0 0 0 1px rgba(102, 126, 234, 0.4) inset;
}

:deep(.el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 1px #667eea inset, 0 0 0 3px rgba(102, 126, 234, 0.15);
}

/* 发送按钮增强 */
:deep(.el-input-group__append) {
  border-radius: 24px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border: none;
  padding: 0;
  overflow: hidden;
}

:deep(.el-input-group__append .el-button) {
  border-radius: 24px;
  background: transparent;
  border: none;
  color: #fff;
  font-weight: 500;
  padding: 8px 20px;
  transition: all 0.3s ease;
}

:deep(.el-input-group__append .el-button:hover) {
  background: rgba(255, 255, 255, 0.1);
}

:deep(.el-input-group__append .el-button.is-loading) {
  background: transparent;
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

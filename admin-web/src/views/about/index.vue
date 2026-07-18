<template>
  <div class="page-container">
    <div class="page-header">
      <h2>关于与版本</h2>
      <el-button @click="fetchAbout" :loading="loading">
        <el-icon><Refresh /></el-icon>刷新
      </el-button>
    </div>

    <el-alert v-if="error" type="error" :closable="false" show-icon style="margin-bottom: 16px;">
      {{ error }}
    </el-alert>

    <!-- 1. 当前版本、发布日期和更新摘要 -->
    <el-card class="info-card">
      <template #header>
        <div class="card-header">
          <span>当前版本</span>
          <el-tag v-if="buildTimeVersion" type="info" size="small">构建时: {{ buildTimeVersion }}</el-tag>
        </div>
      </template>
      <el-descriptions :column="2" border v-loading="loading">
        <el-descriptions-item label="产品名称">
          {{ about?.product_name || 'Aion Memory Nexus · 永识中枢' }}
        </el-descriptions-item>
        <el-descriptions-item label="产品版本">
          <el-tag type="success">{{ about?.product_version || buildTimeVersion || '—' }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="发布日期">
          {{ about?.release_notes?.date || '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="API 版本">
          {{ about?.api_version || '—' }}
        </el-descriptions-item>
      </el-descriptions>
      <div v-if="about?.release_notes?.summary" class="section-summary">
        <strong>更新摘要：</strong>{{ about.release_notes.summary }}
      </div>
    </el-card>

    <!-- 2. 当前版本亮点 -->
    <el-card class="info-card">
      <template #header>
        <div class="card-header">
          <span>当前版本亮点</span>
        </div>
      </template>
      <div v-if="highlights.length > 0" class="highlights-list">
        <div v-for="(item, idx) in highlights" :key="idx" class="highlight-item">
          <el-icon class="highlight-icon"><CircleCheckFilled /></el-icon>
          <span>{{ item }}</span>
        </div>
      </div>
      <div v-else class="empty-text">
        <el-empty description="暂无版本亮点信息" :image-size="60" />
      </div>
    </el-card>

    <!-- 3. API/数据库 schema 兼容状态 -->
    <el-card class="info-card">
      <template #header>
        <div class="card-header">
          <span>API / Schema 状态</span>
          <el-tag :type="schemaDetected ? 'success' : 'warning'" size="small">
            {{ schemaDetected ? '已检测' : '未检测' }}
          </el-tag>
        </div>
      </template>
      <el-descriptions :column="1" border v-loading="loading">
        <el-descriptions-item label="API 版本">
          {{ about?.api_version || '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="Schema Revision">
          {{ about?.schema_revision || '未迁移' }}
        </el-descriptions-item>
      </el-descriptions>
    </el-card>

    <!-- 4. 当前构建和运行环境（脱敏） -->
    <el-card class="info-card">
      <template #header>
        <div class="card-header">
          <span>构建与运行环境</span>
          <el-tag type="info" size="small">脱敏信息</el-tag>
        </div>
      </template>
      <el-descriptions :column="2" border v-loading="loading">
        <el-descriptions-item label="Build Commit">
          <code class="commit-hash">{{ about?.build_commit || 'unknown' }}</code>
        </el-descriptions-item>
        <el-descriptions-item label="构建时间">
          {{ about?.built_at || 'unknown' }}
        </el-descriptions-item>
        <el-descriptions-item label="运行环境">
          <el-tag :type="envTagType" size="small">{{ about?.environment || '—' }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="Runtime Profiles">
          <el-tag
            v-for="profile in (about?.runtime_profiles || [])"
            :key="profile"
            size="small"
            style="margin-right: 4px;"
          >
            {{ profile }}
          </el-tag>
          <span v-if="!about?.runtime_profiles?.length">—</span>
        </el-descriptions-item>
      </el-descriptions>
    </el-card>

    <!-- 5. 最近版本记录和详细发布说明入口 -->
    <el-card class="info-card">
      <template #header>
        <div class="card-header">
          <span>版本记录</span>
        </div>
      </template>
      <div v-if="about?.release_notes" class="release-notes">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="版本">
            {{ about.release_notes.version }}
          </el-descriptions-item>
          <el-descriptions-item v-if="about.release_notes.title" label="标题">
            {{ about.release_notes.title }}
          </el-descriptions-item>
          <el-descriptions-item v-if="about.release_notes.date" label="日期">
            {{ about.release_notes.date }}
          </el-descriptions-item>
        </el-descriptions>
        <div v-if="about.release_notes.highlights.length > 0" class="highlights-list" style="margin-top: 12px;">
          <div v-for="(item, idx) in about.release_notes.highlights" :key="idx" class="highlight-item">
            <el-icon class="highlight-icon"><Star /></el-icon>
            <span>{{ item }}</span>
          </div>
        </div>
      </div>
      <div v-else class="empty-text">
        <el-empty description="暂无发布说明" :image-size="60" />
      </div>
      <div class="release-link">
        <el-link type="primary" underline="never" @click="copyReleasePath">
          <el-icon><Link /></el-icon>
          详细发布说明：docs/releases/
        </el-link>
      </div>
    </el-card>

    <!-- 6. 诊断信息复制按钮 -->
    <el-card class="info-card">
      <template #header>
        <div class="card-header">
          <span>诊断信息</span>
          <el-button type="primary" size="small" @click="copyDiagnostics" :disabled="!about">
            <el-icon><CopyDocument /></el-icon>复制脱敏 JSON
          </el-button>
        </div>
      </template>
      <div class="diagnostics-preview">
        <pre>{{ diagnosticsJson }}</pre>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Refresh, CircleCheckFilled, Star, Link, CopyDocument
} from '@element-plus/icons-vue'
import { aboutApi, type AboutInfo } from '../../api'

// 构建时由 vite.config.ts define 注入（来自根目录 VERSION 文件）
const buildTimeVersion = __APP_VERSION__

const loading = ref(false)
const error = ref<string | null>(null)
const about = ref<AboutInfo | null>(null)

const highlights = computed(() => {
  if (about.value?.release_notes?.highlights?.length) {
    return about.value.release_notes.highlights
  }
  return []
})

// 当前 API 只证明数据库 revision 可读取，尚未与部署期 expected head 比较。
// 因此这里使用“已检测”，避免把任意非空 revision 误报为“兼容”。
const schemaDetected = computed(() => {
  return !!(about.value?.api_version && about.value?.schema_revision)
})

const envTagType = computed<'success' | 'warning' | 'danger'>(() => {
  const env = about.value?.environment?.toLowerCase() || ''
  if (env === 'production') return 'success'
  if (env === 'testing' || env === 'staging') return 'warning'
  if (env === 'development') return 'info'
  return 'info'
})

const diagnosticsJson = computed(() => {
  if (!about.value) return '// 加载中...'
  // 仅包含脱敏字段（API 已经过滤，这里再保险一层）
  const safe: Record<string, unknown> = {
    product_name: about.value.product_name,
    product_version: about.value.product_version,
    api_version: about.value.api_version,
    schema_revision: about.value.schema_revision,
    build_commit: about.value.build_commit,
    built_at: about.value.built_at,
    environment: about.value.environment,
    runtime_profiles: about.value.runtime_profiles,
  }
  return JSON.stringify(safe, null, 2)
})

const fetchAbout = async () => {
  loading.value = true
  error.value = null
  try {
    about.value = await aboutApi.get()
  } catch (e: any) {
    error.value = e.message || '获取版本信息失败'
    ElMessage.error(error.value)
  } finally {
    loading.value = false
  }
}

const copyDiagnostics = async () => {
  try {
    await navigator.clipboard.writeText(diagnosticsJson.value)
    ElMessage.success('诊断信息已复制到剪贴板')
  } catch (e) {
    // Fallback for older browsers
    const textarea = document.createElement('textarea')
    textarea.value = diagnosticsJson.value
    document.body.appendChild(textarea)
    textarea.select()
    try {
      document.execCommand('copy')
      ElMessage.success('诊断信息已复制到剪贴板')
    } catch {
      ElMessage.error('复制失败，请手动选择文本')
    }
    document.body.removeChild(textarea)
  }
}

const copyReleasePath = () => {
  ElMessage.info('发布说明位于仓库 docs/releases/ 目录')
}

onMounted(() => {
  fetchAbout()
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
  display: flex;
  justify-content: space-between;
  align-items: center;
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

/* 版本亮点列表 */
.highlights-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.highlight-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 8px;
  background: rgba(102, 126, 234, 0.04);
  transition: all 0.3s ease;
}

.highlight-item:hover {
  background: rgba(102, 126, 234, 0.08);
  transform: translateX(4px);
}

.highlight-icon {
  color: #667eea;
  flex-shrink: 0;
}

/* 摘要 */
.section-summary {
  margin-top: 16px;
  padding: 12px 16px;
  background: rgba(102, 126, 234, 0.05);
  border-radius: 8px;
  color: #1a1a2e;
  font-size: 14px;
  line-height: 1.6;
}

/* 空状态 */
.empty-text {
  padding: 20px 0;
}

/* Commit hash 样式 */
.commit-hash {
  font-family: 'Courier New', monospace;
  background: rgba(102, 126, 234, 0.08);
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 13px;
  color: #667eea;
}

/* 发布说明链接 */
.release-link {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid rgba(102, 126, 234, 0.1);
}

/* 诊断信息预览 */
.diagnostics-preview {
  background: #1a1a2e;
  border-radius: 8px;
  padding: 16px;
  overflow-x: auto;
}

.diagnostics-preview pre {
  margin: 0;
  color: #a5d6ff;
  font-family: 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
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

<template>
  <section class="settings-page">
    <LmPageHeader
      title="设置"
      subtitle="只展示会真实生效的个人连接与数据选项；系统策略和运行参数保持受控。"
    />

    <div class="settings-intro" role="note">
      <span class="settings-intro__eyebrow">PERSONAL CONTROL</span>
      <p>连接你的工具、选择已配置的模型、管理数据与外部接入。内置对话与工作 Agent 的权限、Prompt 和治理规则不在这里修改。</p>
    </div>

    <LmSettingsSection title="连接" description="企业微信与 Obsidian 的连接状态和同步入口。">
      <div class="settings-grid">
        <RouterLink class="settings-card" to="/wecom">
          <span class="settings-card__mark">01</span>
          <span><strong>企业微信</strong><small>查看机器人状态、测试消息与主动性边界。</small></span>
          <el-icon><ArrowRight /></el-icon>
        </RouterLink>
        <RouterLink class="settings-card" to="/obsidian">
          <span class="settings-card__mark">02</span>
          <span><strong>Obsidian 同步</strong><small>管理本地知识库连接与同步范围。</small></span>
          <el-icon><ArrowRight /></el-icon>
        </RouterLink>
      </div>
    </LmSettingsSection>

    <LmSettingsSection title="AI 模型" description="选择和测试已配置的模型提供商；密钥只保存在服务端。">
      <RouterLink class="settings-card settings-card--wide" to="/llm-providers">
        <span class="settings-card__mark">03</span>
        <span><strong>模型与提供商</strong><small>查看默认模型、已配置提供商和连通性测试。</small></span>
        <el-icon><ArrowRight /></el-icon>
      </RouterLink>
    </LmSettingsSection>

    <LmSettingsSection title="对话主动性" description="这些边界由价值驱动的 Heartbeat 读取；没有候选时不会调用模型或发送消息。">
      <div class="question-preferences" v-loading="preferencesLoading">
        <div class="question-preference-row">
          <div><strong>允许主动联系</strong><small>关闭后，系统不会主动发送跟进；正常聊天不受影响。</small></div>
          <el-switch v-model="conversationPreferences.enabled" :disabled="preferencesSaving" @change="saveConversationPreferences" />
        </div>
        <div class="question-preference-row">
          <div><strong>安静时段</strong><small>在这个时间段内不主动打扰；跨午夜也会正确生效。</small></div>
          <div class="quiet-hours">
            <el-select v-model="conversationPreferences.quiet_hours_start" :disabled="preferencesSaving" aria-label="安静时段开始" @change="saveConversationPreferences">
              <el-option :value="null" label="不限制" />
              <el-option v-for="hour in hours" :key="`start-${hour}`" :value="hour" :label="`${String(hour).padStart(2, '0')}:00`" />
            </el-select>
            <span>至</span>
            <el-select v-model="conversationPreferences.quiet_hours_end" :disabled="preferencesSaving" aria-label="安静时段结束" @change="saveConversationPreferences">
              <el-option :value="null" label="不限制" />
              <el-option v-for="hour in hours" :key="`end-${hour}`" :value="hour" :label="`${String(hour).padStart(2, '0')}:00`" />
            </el-select>
          </div>
        </div>
        <div class="question-preference-row">
          <div><strong>主动强度</strong><small>只调整候选价值阈值；每日最多 2 次、至少间隔 6 小时的硬限制不会改变。</small></div>
          <el-select v-model="conversationPreferences.intensity" :disabled="preferencesSaving" aria-label="主动强度" @change="saveConversationPreferences">
            <el-option label="克制" value="low" />
            <el-option label="标准" value="normal" />
            <el-option label="积极" value="high" />
          </el-select>
        </div>
      </div>
    </LmSettingsSection>

    <LmSettingsSection title="数据与外部接入" description="管理外部 Agent 接入、数据同步和版本兼容信息。">
      <div class="settings-grid">
        <RouterLink class="settings-card" to="/agents">
          <span class="settings-card__mark">04</span>
          <span><strong>外部 Agent 接入</strong><small>管理 MCP、Token、召回范围与同步状态。</small></span>
          <el-icon><ArrowRight /></el-icon>
        </RouterLink>
        <RouterLink class="settings-card" to="/about">
          <span class="settings-card__mark">05</span>
          <span><strong>关于与版本</strong><small>查看产品版本、Schema 兼容与发布说明。</small></span>
          <el-icon><ArrowRight /></el-icon>
        </RouterLink>
      </div>
    </LmSettingsSection>

    <LmSettingsSection
      title="高级诊断"
      description="低频维护入口默认收起；它们不属于日常记忆管理。"
      :default-collapsed="true"
    >
      <div class="settings-grid">
        <RouterLink class="settings-card" to="/orchestration">
          <span><strong>Runtime 实验室</strong><small>查看编排与运行诊断，不改变系统权限。</small></span>
          <el-icon><ArrowRight /></el-icon>
        </RouterLink>
        <RouterLink class="settings-card" to="/events">
          <span><strong>原始事件</strong><small>仅在需要排查采集来源时使用。</small></span>
          <el-icon><ArrowRight /></el-icon>
        </RouterLink>
      </div>
    </LmSettingsSection>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ArrowRight } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { wecomApi } from '../../api'
import LmPageHeader from '../../components/LmPageHeader.vue'
import LmSettingsSection from '../../components/LmSettingsSection.vue'

const hours = Array.from({ length: 24 }, (_, hour) => hour)
const preferencesLoading = ref(true)
const preferencesSaving = ref(false)
const conversationPreferences = reactive({
  enabled: true,
  quiet_hours_start: 22 as number | null,
  quiet_hours_end: 8 as number | null,
  intensity: 'normal',
})

const loadConversationPreferences = async () => {
  preferencesLoading.value = true
  try {
    const response = await wecomApi.conversationPreferences()
    Object.assign(conversationPreferences, response)
  } catch (error: any) {
    ElMessage.error(error?.message || '主动提问偏好加载失败')
  } finally {
    preferencesLoading.value = false
  }
}

const saveConversationPreferences = async () => {
  preferencesSaving.value = true
  try {
    const response = await wecomApi.updateConversationPreferences({ ...conversationPreferences })
    Object.assign(conversationPreferences, response)
    ElMessage.success('对话主动性边界已保存')
  } catch (error: any) {
    ElMessage.error(error?.message || '保存失败，已保留原设置')
    await loadConversationPreferences()
  } finally {
    preferencesSaving.value = false
  }
}

onMounted(loadConversationPreferences)
</script>

<style scoped>
.settings-page { max-width: 960px; margin: 0 auto; }
.settings-intro { margin: 0 0 20px; padding: 18px 20px; border-left: 3px solid var(--lm-color-primary); background: var(--lm-color-bg-muted); color: var(--lm-color-text-secondary); }
.settings-intro__eyebrow { display: block; margin-bottom: 7px; color: var(--lm-color-primary); font-size: 11px; font-weight: 700; letter-spacing: .12em; }
.settings-intro p { margin: 0; line-height: 1.7; }
.settings-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.settings-card { display: flex; align-items: center; gap: 13px; min-height: 82px; padding: 16px; border: 1px solid var(--lm-color-border); border-radius: var(--lm-radius-sm); color: inherit; text-decoration: none; transition: border-color var(--lm-transition), background var(--lm-transition), transform var(--lm-transition); }
.settings-card:hover { border-color: var(--lm-color-primary); background: var(--lm-color-bg-muted); transform: translateY(-1px); }
.settings-card span:not(.settings-card__mark) { display: grid; gap: 5px; flex: 1; min-width: 0; }
.settings-card strong { color: var(--lm-color-text); font-size: var(--lm-font-size-base); }
.settings-card small { color: var(--lm-color-text-secondary); line-height: 1.45; }
.settings-card__mark { color: var(--lm-color-primary); font-family: ui-monospace, monospace; font-size: 12px; }
.settings-card--wide { width: 100%; }
.question-preferences { display: grid; border: 1px solid var(--lm-color-border); border-radius: var(--lm-radius-sm); overflow: hidden; }
.question-preference-row { display: flex; align-items: center; justify-content: space-between; gap: 20px; min-height: 82px; padding: 16px; border-bottom: 1px solid var(--lm-color-border); }
.question-preference-row:last-child { border-bottom: 0; }
.question-preference-row > div:first-child { display: grid; gap: 5px; max-width: 560px; }
.question-preference-row strong { color: var(--lm-color-text); }
.question-preference-row small { color: var(--lm-color-text-secondary); line-height: 1.45; }
.quiet-hours { display: flex; align-items: center; gap: 8px; min-width: 230px; }
.quiet-hours .el-select { width: 106px; }
@media (max-width: 767px) { .settings-grid { grid-template-columns: 1fr; } .settings-card { min-height: 74px; } }
@media (max-width: 767px) { .question-preference-row { align-items: flex-start; flex-direction: column; gap: 12px; } .quiet-hours { width: 100%; } }
</style>

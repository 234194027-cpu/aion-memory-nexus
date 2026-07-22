<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <h2>Agent 记忆接入</h2>
        <p>管理外部 Agent 接入、MCP 配置、同步状态和服务端提交策略。</p>
      </div>
      <el-button type="primary" @click="createVisible = true">
        <el-icon><Plus /></el-icon>
        新建 Agent
      </el-button>
    </div>

    <el-card class="runtime-card" v-loading="runtimeLoading">
      <template #header>
        <div class="section-title">
        <h3>双 Agent V2.5.3</h3>
          <el-tag type="success">自主记忆治理</el-tag>
        </div>
      </template>
      <dl class="runtime-grid">
        <div><dt>活跃案件</dt><dd>{{ workingStatus?.active_backlog ?? '-' }}</dd></div>
        <div><dt>队列积压</dt><dd>{{ workingStatus?.queue_backlog ?? '-' }}</dd></div>
        <div><dt>等待补证</dt><dd>{{ workingStatus?.waiting_for_evidence ?? '-' }}</dd></div>
        <div><dt>治理决策</dt><dd>{{ workingStatus?.decision_count ?? '-' }}</dd></div>
        <div><dt>自动记忆</dt><dd>{{ workingStatus?.automatic_memory_count ?? '-' }}</dd></div>
        <div><dt>失败事件</dt><dd>{{ workingStatus?.failed_event_count ?? '-' }}</dd></div>
        <div><dt>待重试</dt><dd>{{ workingStatus?.retryable_failed_event_count ?? '-' }}</dd></div>
        <div><dt>平均处理</dt><dd>{{ formatDuration(workingStatus?.average_processing_ms) }}</dd></div>
        <div><dt>正式写入</dt><dd>工作 Agent 自动治理</dd></div>
        <div><dt>正式记忆摘要</dt><dd>{{ workingStatus?.memory_brief?.memory_count ?? '-' }} 条</dd></div>
        <div><dt>摘要刷新</dt><dd>{{ formatTime(workingStatus?.memory_brief?.generated_at) }}</dd></div>
        <div><dt>证据封存</dt><dd>{{ workingStatus?.evidence_seal_count ?? '-' }}</dd></div>
        <div><dt>自动合并</dt><dd>{{ workingStatus?.maintenance_actions?.merge ?? 0 }}</dd></div>
        <div><dt>来源清理</dt><dd>{{ (workingStatus?.maintenance_actions?.compact ?? 0) + (workingStatus?.maintenance_actions?.purge ?? 0) }}</dd></div>
        <div><dt>维护 Token</dt><dd>{{ workingStatus?.maintenance_token_used ?? 0 }}</dd></div>
        <div><dt>维护安全状态</dt><dd>{{ maintenanceStateLabel(maintenanceControl?.state) }}</dd></div>
        <div><dt>文档来源检索</dt><dd>{{ workingStatus?.shared_cognition?.document_source_search ? '已启用' : '未启用' }}</dd></div>
        <div><dt>未确认线索</dt><dd>{{ workingStatus?.shared_cognition?.unconfirmed_clue_search ? '仅限澄清' : '未启用' }}</dd></div>
      </dl>
      <div class="runtime-actions">
        <el-button
          v-if="maintenanceControl?.state === 'active'"
          type="warning"
          @click="pauseMaintenance"
        >暂停高风险维护</el-button>
        <el-button
          v-else
          type="primary"
          @click="resumeMaintenance"
        >进入 Shadow 恢复</el-button>
        <span>{{ maintenanceControl?.pause_reason || '对话与事件采集不会被维护开关中断。' }}</span>
      </div>
    </el-card>

    <el-card class="table-card maintenance-card">
      <template #header><div class="section-title"><h3>最近维护动作</h3><span>自动合并、替代和过期保留 30 天回滚窗口</span></div></template>
      <el-table :data="maintenanceActions" row-key="id" empty-text="暂无维护动作">
        <el-table-column prop="action" label="动作" width="110" />
        <el-table-column prop="state" label="状态" width="120" />
        <el-table-column prop="reason_code" label="依据" min-width="220" />
        <el-table-column label="执行时间" min-width="180"><template #default="{ row }">{{ formatTime(row.created_at) }}</template></el-table-column>
        <el-table-column label="可回滚至" min-width="180"><template #default="{ row }">{{ formatTime(row.reversible_until) }}</template></el-table-column>
        <el-table-column label="操作" width="110">
          <template #default="{ row }">
            <el-button
              v-if="isRollbackAvailable(row)"
              type="warning"
              link
              @click="rollbackAction(row)"
            >回滚</el-button>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card class="table-card">
      <el-table :data="agentList" v-loading="loading" row-key="id">
        <el-table-column prop="agent_name" label="Agent 名称" min-width="180" />
        <el-table-column prop="agent_type" label="类型" width="130">
          <template #default="{ row }">
            {{ agentTypeLabel(row.agent_type) }}
          </template>
        </el-table-column>
        <el-table-column prop="default_recall_level" label="召回级别" width="160">
          <template #default="{ row }">
            {{ recallLevelLabel(row.default_recall_level) }}
          </template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="row.status ? 'success' : 'info'">
              {{ row.status ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最后活跃" width="190">
          <template #default="{ row }">
            {{ formatTime(row.last_seen_at) }}
          </template>
        </el-table-column>
        <el-table-column label="策略" min-width="220">
          <template #default="{ row }">
            <el-tag
              v-for="policy in enabledPolicies(row)"
              :key="policy.description || policy.type"
              class="policy-tag"
              type="warning"
            >
              {{ policyLabel(policy) }}
            </el-tag>
            <span v-if="!enabledPolicies(row).length" class="muted">服务端治理</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="300" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link @click="openBridge(row)">接入配置</el-button>
            <el-button type="primary" link @click="handleRegenerateToken(row)">重置 Token</el-button>
            <el-button type="danger" link @click="handleDelete(row)" :disabled="row.is_default">停用</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="createVisible" title="新建 Agent" width="520px">
      <el-form :model="createForm" label-width="130px">
        <el-form-item label="名称" required>
          <el-input v-model="createForm.agent_name" placeholder="例如：Codex 记忆助手" />
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="createForm.agent_type">
            <el-option label="Codex" value="codex" />
            <el-option label="Claude Code" value="claude_code" />
            <el-option label="OpenClaw" value="openclaw" />
            <el-option label="自定义" value="custom" />
          </el-select>
        </el-form-item>
        <el-form-item label="召回级别">
          <el-select v-model="createForm.default_recall_level">
            <el-option label="仅任务上下文" value="task_only" />
            <el-option label="工作上下文" value="work_context" />
            <el-option label="个人上下文" value="personal_context" />
            <el-option label="完整可信上下文" value="full_trusted" />
          </el-select>
        </el-form-item>
        <el-form-item label="角色">
          <el-input v-model="createForm.role" />
        </el-form-item>
        <el-form-item label="使命">
          <el-input v-model="createForm.mission" type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleCreate">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="tokenVisible" title="Token 仅显示一次" width="680px">
      <el-alert type="warning" :closable="false" show-icon>
        此 Token 只会显示一次。请保存到 MCP 客户端环境变量中，不要提交到代码仓库。
      </el-alert>
      <pre class="code-block">{{ oneTimeToken }}</pre>
      <template #footer>
        <el-button @click="copy(oneTimeToken)">复制 Token</el-button>
        <el-button type="primary" @click="tokenVisible = false">完成</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="bridgeVisible" size="720px" title="MCP 接入配置">
      <div v-if="bridge" class="bridge-content">
        <section class="section-panel">
          <h3>Agent</h3>
          <dl class="meta-grid">
            <div><dt>ID</dt><dd>{{ bridge.agent.id }}</dd></div>
            <div><dt>类型</dt><dd>{{ agentTypeLabel(bridge.agent.agent_type) }}</dd></div>
            <div><dt>召回级别</dt><dd>{{ recallLevelLabel(bridge.agent.default_recall_level) }}</dd></div>
            <div><dt>最后活跃</dt><dd>{{ formatTime(bridge.agent.last_seen_at) }}</dd></div>
          </dl>
        </section>

        <section class="section-panel">
          <div class="section-title">
            <h3>MCP 配置</h3>
            <el-button link type="primary" @click="copy(bridge.mcp_config)">复制</el-button>
          </div>
          <pre class="code-block">{{ bridge.mcp_config }}</pre>
        </section>

        <section class="section-panel">
          <div class="section-title">
            <h3>外部 Agent 系统提示词</h3>
            <el-button link type="primary" @click="copy(bridge.external_agent_prompt)">复制</el-button>
          </div>
          <pre class="code-block">{{ bridge.external_agent_prompt }}</pre>
        </section>

        <section class="section-panel">
          <div class="section-title">
            <h3>一次性 MCP 测试提示词</h3>
            <el-button link type="primary" @click="copy(bridge.mcp_test_prompt)">复制</el-button>
          </div>
          <pre class="code-block">{{ bridge.mcp_test_prompt }}</pre>
        </section>

        <section class="section-panel">
          <h3>同步状态</h3>
          <dl class="meta-grid">
            <div><dt>原始事件</dt><dd>{{ bridge.sync_status.raw_event_count }}</dd></div>
            <div><dt>工作案件</dt><dd>{{ bridge.sync_status.work_case_count }}</dd></div>
            <div><dt>已提交记忆</dt><dd>{{ bridge.sync_status.committed_count }}</dd></div>
            <div><dt>跳过重复</dt><dd>{{ bridge.sync_status.duplicate_skipped_count }}</dd></div>
            <div><dt>上次同步</dt><dd>{{ formatTime(bridge.sync_status.last_sync_at) }}</dd></div>
          </dl>
          <el-alert
            v-if="bridge.sync_status.recent_errors?.length"
            type="error"
            :closable="false"
            show-icon
            title="检测到最近同步错误"
          />
        </section>

        <section class="section-panel">
          <h3>策略</h3>
          <el-tag type="success">工作 Agent 自动治理，来源可追溯</el-tag>
          <pre class="code-block">{{ JSON.stringify(bridge.policy_status.allowed_write_scopes, null, 2) }}</pre>
        </section>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { agentsApi, runtimeApi } from '../../api'
import { agentTypeLabel, recallLevelLabel } from '../../utils/labels'

const loading = ref(false)
const saving = ref(false)
const agentList = ref<any[]>([])
const createVisible = ref(false)
const tokenVisible = ref(false)
const bridgeVisible = ref(false)
const oneTimeToken = ref('')
const bridge = ref<any>(null)
const workingStatus = ref<any>(null)
const maintenanceControl = ref<any>(null)
const maintenanceActions = ref<any[]>([])
const runtimeLoading = ref(false)

const createForm = reactive({
  agent_name: '',
  agent_type: 'custom',
  default_recall_level: 'work_context',
  role: '',
  mission: ''
})

const normalizeList = (res: any) => {
  if (Array.isArray(res)) return res
  return res?.items || res?.agents || []
}

const fetchData = async () => {
  loading.value = true
  runtimeLoading.value = true
  try {
    const [res, status, control, actions] = await Promise.all([
      agentsApi.list(),
      agentsApi.workingStatus(),
      runtimeApi.maintenanceControl(),
      runtimeApi.maintenanceActions(20)
    ])
    agentList.value = normalizeList(res)
    workingStatus.value = status
    maintenanceControl.value = control
    maintenanceActions.value = actions?.items || []
  } finally {
    loading.value = false
    runtimeLoading.value = false
  }
}

const maintenanceStateLabel = (state?: string) => ({
  active: '自动维护中',
  shadow: 'Shadow 验证',
  paused_automatically: '已自动熔断',
  paused_manually: '已手动暂停',
  recovering: '恢复验证中'
}[state || ''] || state || '自动维护中')

const pauseMaintenance = async () => {
  const { value } = await ElMessageBox.prompt('请填写暂停原因，正常对话和事件采集不会停止。', '暂停高风险维护', {
    confirmButtonText: '确认暂停', cancelButtonText: '取消', inputValue: '管理员主动检查'
  })
  await runtimeApi.pauseMaintenance(value)
  ElMessage.success('高风险维护已暂停')
  await fetchData()
}

const resumeMaintenance = async () => {
  const { value } = await ElMessageBox.prompt('恢复前会先执行一次来源完整性 Shadow 验证。', '恢复自动维护', {
    confirmButtonText: '开始验证', cancelButtonText: '取消', inputValue: '问题已排查'
  })
  await runtimeApi.resumeMaintenance(value)
  ElMessage.success('已进入恢复验证状态')
  await fetchData()
}

const isRollbackAvailable = (row: any) => {
  if (!['merge', 'supersede', 'expire'].includes(row.action) || row.state !== 'completed' || !row.reversible_until) return false
  return new Date(row.reversible_until).getTime() > Date.now()
}

const rollbackAction = async (row: any) => {
  const { value } = await ElMessageBox.prompt('回滚会恢复原记忆状态并刷新摘要与索引，请填写原因。', '回滚维护动作', {
    confirmButtonText: '确认回滚', cancelButtonText: '取消', inputValue: '自动治理结果需要撤回'
  })
  await runtimeApi.rollbackMaintenance(row.id, value)
  ElMessage.success('维护动作已回滚')
  await fetchData()
}

const handleCreate = async () => {
  if (!createForm.agent_name.trim()) {
    ElMessage.warning('请输入 Agent 名称')
    return
  }
  saving.value = true
  try {
    const res = await agentsApi.create({ ...createForm })
    oneTimeToken.value = res.api_token || ''
    tokenVisible.value = Boolean(oneTimeToken.value)
    createVisible.value = false
    createForm.agent_name = ''
    createForm.role = ''
    createForm.mission = ''
    await fetchData()
  } finally {
    saving.value = false
  }
}

const openBridge = async (row: any) => {
  bridge.value = await agentsApi.bridgeStatus(row.id)
  bridgeVisible.value = true
}

const handleRegenerateToken = async (row: any) => {
  await ElMessageBox.confirm(`确定要重置「${row.agent_name}」的 Token 吗？已有 MCP 客户端会停止工作。`, '重置 Token', {
    confirmButtonText: '确定重置',
    cancelButtonText: '取消',
    type: 'warning'
  })
  const res = await agentsApi.regenerateToken(row.id)
  oneTimeToken.value = res.api_token || ''
  tokenVisible.value = Boolean(oneTimeToken.value)
}

const handleDelete = async (row: any) => {
  await ElMessageBox.confirm(`确定要停用 Agent「${row.agent_name}」吗？`, '停用 Agent', {
    confirmButtonText: '确定停用',
    cancelButtonText: '取消',
    type: 'warning'
  })
  await agentsApi.delete(row.id)
  await fetchData()
}

const enabledPolicies = (row: any) => {
  return (row.allowed_write_scopes || []).filter((policy: any) => policy.enabled)
}

const policyLabel = (policy: any) => {
  return policy?.description || policy?.type || '未知策略'
}

const formatTime = (value?: string) => {
  if (!value) return '-'
  return new Date(value).toLocaleString()
}

const formatDuration = (value?: number | null) => {
  if (value === null || value === undefined) return '-'
  if (value < 1000) return `${Math.round(value)} ms`
  return `${(value / 1000).toFixed(2)} 秒`
}

const copy = async (text: string) => {
  await navigator.clipboard.writeText(text || '')
  ElMessage.success('已复制')
}

onMounted(fetchData)
</script>

<style scoped>
.page-container {
  padding: 20px;
  max-width: 1600px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 24px;
  margin-bottom: 20px;
}

.page-header h2 {
  margin: 0;
  font-size: 22px;
  font-weight: 650;
  color: #172033;
}

.page-header p {
  margin: 6px 0 0;
  color: #667085;
}

.table-card {
  border-radius: 8px;
}

.runtime-card {
  margin-bottom: 20px;
  border-radius: 8px;
}

.runtime-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
  margin: 0;
}

.runtime-grid > div {
  padding: 12px;
  border-radius: 8px;
  background: #f8fafc;
}

.runtime-grid dt {
  color: #667085;
  font-size: 12px;
}

.runtime-grid dd {
  margin: 6px 0 0;
  color: #172033;
  font-size: 18px;
  font-weight: 650;
}

.runtime-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid #e4e7ec;
  color: #667085;
  font-size: 13px;
}

.maintenance-card {
  margin-bottom: 20px;
}

.policy-tag {
  margin-right: 6px;
  margin-bottom: 4px;
}

.muted {
  color: #98a2b3;
}

.bridge-content {
  display: grid;
  gap: 16px;
}

.section-panel {
  border: 1px solid #e4e7ec;
  border-radius: 8px;
  padding: 14px;
  background: #fff;
}

.section-panel h3 {
  margin: 0 0 10px;
  font-size: 15px;
  color: #172033;
}

.section-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.meta-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin: 0;
}

.meta-grid dt {
  color: #667085;
  font-size: 12px;
}

.meta-grid dd {
  margin: 4px 0 0;
  color: #172033;
  word-break: break-all;
}

.code-block {
  margin: 10px 0 0;
  padding: 12px;
  border-radius: 6px;
  background: #101828;
  color: #f9fafb;
  font-size: 12px;
  line-height: 1.5;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}

@media (max-width: 767px) {
  .page-container { padding: 0; }
  .page-header, .runtime-actions { align-items: flex-start; flex-direction: column; }
  .runtime-grid { grid-template-columns: 1fr; }
}
</style>

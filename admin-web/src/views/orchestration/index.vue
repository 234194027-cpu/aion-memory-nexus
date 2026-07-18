<template>
  <div class="page-container">
    <div class="page-header">
      <h2>任务编排</h2>
      <el-button type="primary" @click="showRunDialog = true">
        <el-icon><Plus /></el-icon>运行多智能体
      </el-button>
    </div>

    <el-row :gutter="20" class="stats-row">
      <el-col :span="8">
        <el-card class="stat-card">
          <div class="stat-label">可用工具</div>
          <div class="stat-value">{{ stats.toolsCount }}</div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card class="stat-card">
          <div class="stat-label">模拟次数</div>
          <div class="stat-value">{{ stats.simulationsCount }}</div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card class="stat-card">
          <div class="stat-label">权限规则</div>
          <div class="stat-value">{{ stats.permissionsCount }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-card class="table-card">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="推演模拟" name="simulation">
          <div class="tab-toolbar">
            <el-button type="primary" @click="showSimulateDialog = true">新建模拟</el-button>
          </div>
          <el-table :data="simulations" v-loading="loading">
            <el-table-column prop="id" label="运行 ID" min-width="180" show-overflow-tooltip />
            <el-table-column prop="question" label="问题" min-width="260" show-overflow-tooltip />
            <el-table-column prop="confidence" label="置信度" width="100">
              <template #default="{ row }">{{ formatPercent(row.confidence) }}</template>
            </el-table-column>
            <el-table-column prop="created_at" label="创建时间" width="190">
              <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="100">
              <template #default="{ row }">
                <el-button type="primary" link @click="viewSimulation(row)">查看</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="工具" name="tools">
          <el-table :data="tools" v-loading="loading">
            <el-table-column prop="name" label="名称" min-width="180" />
            <el-table-column prop="description" label="描述" min-width="300" show-overflow-tooltip />
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="权限" name="permissions">
          <el-table :data="permissions" v-loading="loading">
            <el-table-column prop="agent_id" label="智能体 ID" min-width="180" show-overflow-tooltip />
            <el-table-column prop="tool_name" label="工具" min-width="180" />
            <el-table-column label="范围" width="100">
              <template #default="{ row }">
                <el-tag :type="row.scope === 'allow' ? 'success' : 'danger'">
                  {{ row.scope === 'allow' ? '允许' : '拒绝' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="创建时间" width="190">
              <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="120">
              <template #default="{ row }">
                <el-button type="primary" link @click="checkPermission(row)">检查</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <el-dialog v-model="showRunDialog" title="运行多智能体" width="560px">
      <el-form :model="runForm" label-width="100px">
        <el-form-item label="任务描述">
          <el-input v-model="runForm.question" type="textarea" rows="5" placeholder="描述要协同处理的问题" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showRunDialog = false">取消</el-button>
        <el-button type="primary" @click="runMultiAgent" :loading="running">运行</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showSimulateDialog" title="新建模拟" width="560px">
      <el-form :model="simulateForm" label-width="100px">
        <el-form-item label="模拟问题">
          <el-input v-model="simulateForm.question" type="textarea" rows="5" placeholder="描述要推演的情景或决策" />
        </el-form-item>
        <el-form-item label="周期">
          <el-input-number v-model="simulateForm.horizon_days" :min="1" :max="365" />
          <span class="form-hint">天</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showSimulateDialog = false">取消</el-button>
        <el-button type="primary" @click="runSimulation" :loading="simulating">开始模拟</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { orchestrationApi } from '../../api'

const loading = ref(false)
const running = ref(false)
const simulating = ref(false)
const activeTab = ref('simulation')
const showRunDialog = ref(false)
const showSimulateDialog = ref(false)

const stats = ref({
  toolsCount: 0,
  simulationsCount: 0,
  permissionsCount: 0
})

const simulations = ref<any[]>([])
const tools = ref<any[]>([])
const permissions = ref<any[]>([])

const runForm = ref({
  question: ''
})

const simulateForm = ref({
  question: '',
  horizon_days: 90
})

const messageFromError = (e: any, fallback: string) => {
  return e?.response?.data?.detail || e?.message || fallback
}

const formatTime = (value?: string) => {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN')
}

const formatPercent = (value?: number) => {
  const numeric = Number(value || 0)
  return `${Math.round(numeric * 100)}%`
}

const fetchData = async () => {
  loading.value = true
  try {
    const [toolsRes, simRes, permRes] = await Promise.all([
      orchestrationApi.listTools(),
      orchestrationApi.listSimulations({ limit: 10 }),
      orchestrationApi.listPermissions({ limit: 10 })
    ])
    tools.value = toolsRes.tools || []
    simulations.value = simRes.simulations || []
    permissions.value = permRes.permissions || []
    stats.value = {
      toolsCount: tools.value.length,
      simulationsCount: simRes.total || simulations.value.length,
      permissionsCount: permRes.total || permissions.value.length
    }
  } catch (e: any) {
    ElMessage.error(messageFromError(e, '获取任务编排数据失败'))
  } finally {
    loading.value = false
  }
}

const runMultiAgent = async () => {
  if (!runForm.value.question.trim()) {
    ElMessage.warning('请输入任务描述')
    return
  }
  running.value = true
  try {
    await orchestrationApi.runMultiAgent({ question: runForm.value.question })
    ElMessage.success('任务已开始执行')
    showRunDialog.value = false
    runForm.value.question = ''
    await fetchData()
  } catch (e: any) {
    ElMessage.error(messageFromError(e, '执行失败'))
  } finally {
    running.value = false
  }
}

const runSimulation = async () => {
  if (!simulateForm.value.question.trim()) {
    ElMessage.warning('请输入模拟问题')
    return
  }
  simulating.value = true
  try {
    await orchestrationApi.simulate({
      question: simulateForm.value.question,
      horizon_days: simulateForm.value.horizon_days
    })
    ElMessage.success('模拟已开始')
    showSimulateDialog.value = false
    simulateForm.value.question = ''
    await fetchData()
  } catch (e: any) {
    ElMessage.error(messageFromError(e, '模拟失败'))
  } finally {
    simulating.value = false
  }
}

const viewSimulation = async (row: any) => {
  try {
    const result = await orchestrationApi.getSimulation(row.id)
    await ElMessageBox.alert(
      `问题：${result.question || '-'}\n\n结果：${result.outcome || result.counterfactual || '-'}\n\n置信度：${formatPercent(result.confidence)}`,
      '模拟详情',
      { confirmButtonText: '知道了' }
    )
  } catch (e: any) {
    ElMessage.error(messageFromError(e, '获取详情失败'))
  }
}

const checkPermission = async (row: any) => {
  try {
    const result = await orchestrationApi.checkPermission({
      agent_id: row.agent_id,
      tool_name: row.tool_name
    })
    ElMessage.success(result.allowed ? '权限检查通过' : `权限被拒绝：${result.source}`)
  } catch (e: any) {
    ElMessage.error(messageFromError(e, '权限检查失败'))
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

.stats-row {
  margin-bottom: 20px;
}

.stat-card {
  text-align: center;
  border-radius: 8px;
}

.stat-label {
  font-size: 14px;
  color: #6b7280;
  margin-bottom: 12px;
  font-weight: 500;
}

.stat-value {
  font-size: 34px;
  font-weight: 700;
  color: #2563eb;
}

.table-card {
  border-radius: 8px;
}

.tab-toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 16px;
}

.form-hint {
  margin-left: 8px;
  color: #6b7280;
}
</style>

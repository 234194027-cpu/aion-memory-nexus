<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <h2>LLM 提供商</h2>
        <p>选择内置大模型后只需填写 API Key，即可保存并测试连接。</p>
      </div>
      <el-button type="primary" @click="showCreateDialog">
        <el-icon><Plus /></el-icon>添加自定义提供商
      </el-button>
    </div>

    <section class="preset-grid" v-loading="loading">
      <el-card v-for="preset in presetList" :key="preset.provider_key" class="preset-card">
        <div class="preset-header">
          <div>
            <h3>{{ preset.provider_name }}</h3>
            <p>{{ preset.description }}</p>
          </div>
          <el-tag :type="preset.is_active ? 'success' : 'info'">
            {{ preset.is_active ? '已连接' : '未连接' }}
          </el-tag>
        </div>
        <el-form label-position="top">
          <el-form-item v-if="preset.base_url_editable" label="Ollama 地址">
            <el-input
              v-model="presetBaseUrls[preset.provider_key]"
              placeholder="例如：http://127.0.0.1:11434 或服务器可访问的内网地址"
            />
          </el-form-item>
          <el-form-item label="模型">
            <el-select v-model="presetModels[preset.provider_key]" style="width: 100%">
              <el-option
                v-for="model in preset.models"
                :key="model"
                :label="model"
                :value="model"
              />
            </el-select>
          </el-form-item>
          <el-form-item v-if="preset.requires_api_key !== false" label="API Key">
            <el-input
              v-model="presetSecrets[preset.provider_key]"
              type="password"
              show-password
              placeholder="填写后可保存或测试连接"
            />
          </el-form-item>
          <el-alert
            v-else
            type="info"
            show-icon
            :closable="false"
            title="Ollama 默认无需 API Key。当前由服务器连接该地址，请确保服务器能访问。"
          />
        </el-form>
        <div class="preset-actions">
          <el-button
            @click="testPreset(preset)"
            :loading="testingKey === preset.provider_key"
          >
            测试连接
          </el-button>
          <el-button
            type="primary"
            @click="savePreset(preset)"
            :loading="savingKey === preset.provider_key"
          >
            保存并连接
          </el-button>
        </div>
      </el-card>
    </section>

    <el-card class="table-card">
      <template #header>
        <div class="card-header">
          <span>已配置提供商</span>
          <el-button text @click="fetchData">刷新</el-button>
        </div>
      </template>
      <el-table :data="providerList" v-loading="loading">
        <el-table-column prop="provider_name" label="名称" min-width="140" />
        <el-table-column label="类型" width="120">
          <template #default="{ row }">{{ providerLabel(row.provider_key) }}</template>
        </el-table-column>
        <el-table-column prop="base_url" label="API 地址" min-width="260" show-overflow-tooltip />
        <el-table-column prop="model_name" label="模型" min-width="180" show-overflow-tooltip />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status ? 'success' : 'info'">
              {{ row.status ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="210" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link @click="handleTest(row)">测试</el-button>
            <el-button type="primary" link @click="handleEdit(row)">编辑</el-button>
            <el-button type="danger" link @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="dialogVisible" :title="isEdit ? '编辑提供商' : '添加自定义提供商'" width="640px">
      <el-form :model="form" label-width="110px">
        <el-form-item label="名称">
          <el-input v-model="form.provider_name" placeholder="例如：公司私有模型" />
        </el-form-item>
        <el-form-item label="标识">
          <el-input
            v-model="form.provider_key"
            :disabled="isEdit"
            placeholder="例如：company_model，只能唯一使用一次"
          />
        </el-form-item>
        <el-form-item label="API 地址">
          <el-input v-model="form.base_url" placeholder="https://example.com/v1" />
        </el-form-item>
        <el-form-item label="接口格式">
          <el-select v-model="form.api_format" style="width: 100%">
            <el-option label="OpenAI 兼容" value="openai" />
            <el-option label="Ollama 本地服务" value="ollama" />
          </el-select>
        </el-form-item>
        <el-form-item label="模型">
          <el-input v-model="form.model_name" placeholder="模型名称" />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input
            v-model="form.api_key"
            type="password"
            placeholder="编辑时留空则清除；测试连接需要填写"
            show-password
          />
        </el-form-item>
        <el-form-item label="状态">
          <el-switch v-model="form.status" active-text="启用" inactive-text="停用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button @click="handleDialogTest" :loading="dialogTesting">测试连接</el-button>
        <el-button type="primary" @click="handleSubmit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { llmProvidersApi } from '../../api'

const loading = ref(false)
const savingKey = ref('')
const testingKey = ref('')
const dialogTesting = ref(false)
const providerList = ref<any[]>([])
const presetList = ref<any[]>([])
const presetSecrets = reactive<Record<string, string>>({})
const presetModels = reactive<Record<string, string>>({})
const presetBaseUrls = reactive<Record<string, string>>({})
const dialogVisible = ref(false)
const isEdit = ref(false)
const form = ref({
  provider_name: '',
  provider_key: '',
  base_url: '',
  model_name: '',
  api_key: '',
  api_format: 'openai',
  status: true
})

const messageFromError = (e: any, fallback: string) => {
  return e?.response?.data?.detail || e?.message || fallback
}

const fetchData = async () => {
  loading.value = true
  try {
    const [providers, presets] = await Promise.all([
      llmProvidersApi.list(),
      llmProvidersApi.presets()
    ])
    providerList.value = providers || []
    presetList.value = presets || []
    for (const preset of presetList.value) {
      if (!presetModels[preset.provider_key]) {
        presetModels[preset.provider_key] = preset.model_name
      }
      if (!presetBaseUrls[preset.provider_key]) {
        presetBaseUrls[preset.provider_key] = preset.base_url
      }
    }
  } catch (e: any) {
    ElMessage.error(messageFromError(e, '获取 LLM 提供商失败'))
  } finally {
    loading.value = false
  }
}

const providerLabel = (key: string) => {
  const preset = presetList.value.find((item) => item.provider_key === key)
  return preset?.provider_name || key
}

const presetPayload = (preset: any) => ({
  provider_name: preset.provider_name,
  provider_key: preset.provider_key,
  base_url: presetBaseUrls[preset.provider_key] || preset.base_url,
  model_name: presetModels[preset.provider_key] || preset.model_name,
  api_key: presetSecrets[preset.provider_key],
  api_format: preset.api_format || 'openai'
})

const requirePresetKey = (preset: any) => {
  if (preset.requires_api_key === false) {
    return true
  }
  if (!presetSecrets[preset.provider_key]) {
    ElMessage.warning(`请先填写 ${preset.provider_name} 的 API Key`)
    return false
  }
  return true
}

const savePreset = async (preset: any) => {
  if (!requirePresetKey(preset)) return
  savingKey.value = preset.provider_key
  try {
    await llmProvidersApi.createFromPreset(preset.provider_key, presetPayload(preset))
    presetSecrets[preset.provider_key] = ''
    ElMessage.success(`${preset.provider_name} 已保存`)
    await fetchData()
  } catch (e: any) {
    ElMessage.error(messageFromError(e, '保存提供商失败'))
  } finally {
    savingKey.value = ''
  }
}

const testPreset = async (preset: any) => {
  if (!requirePresetKey(preset)) return
  testingKey.value = preset.provider_key
  try {
    const result = await llmProvidersApi.testConfig(presetPayload(preset))
    ElMessage.success(result?.message || `${preset.provider_name} 连接成功`)
  } catch (e: any) {
    ElMessage.error(messageFromError(e, '测试连接失败'))
  } finally {
    testingKey.value = ''
  }
}

const showCreateDialog = () => {
  isEdit.value = false
  form.value = {
    provider_key: '',
    provider_name: '',
    base_url: '',
    model_name: '',
    api_key: '',
    api_format: 'openai',
    status: true
  }
  dialogVisible.value = true
}

const handleEdit = (row: any) => {
  isEdit.value = true
  form.value = { ...row, api_key: '', api_format: row.api_format || 'openai' }
  dialogVisible.value = true
}

const handleDialogTest = async () => {
  if (form.value.api_format !== 'ollama' && !form.value.api_key) {
    ElMessage.warning('请先填写 API Key 再测试')
    return
  }
  dialogTesting.value = true
  try {
    const result = await llmProvidersApi.testConfig(form.value)
    ElMessage.success(result?.message || '连接成功')
  } catch (e: any) {
    ElMessage.error(messageFromError(e, '测试连接失败'))
  } finally {
    dialogTesting.value = false
  }
}

const handleTest = async (row: any) => {
  testingKey.value = row.provider_key
  try {
    const result = await llmProvidersApi.test(row.provider_key)
    ElMessage.success(result?.message || '连接成功')
  } catch (e: any) {
    ElMessage.error(messageFromError(e, '测试连接失败'))
  } finally {
    testingKey.value = ''
  }
}

const handleSubmit = async () => {
  try {
    if (isEdit.value) {
      await llmProvidersApi.update(form.value.provider_key, form.value)
      ElMessage.success('更新成功')
    } else {
      await llmProvidersApi.create(form.value)
      ElMessage.success('添加成功')
    }
    dialogVisible.value = false
    await fetchData()
  } catch (e: any) {
    ElMessage.error(messageFromError(e, '保存失败'))
  }
}

const handleDelete = async (row: any) => {
  try {
    await ElMessageBox.confirm(`确定要删除提供商“${row.provider_name}”吗？`, '提示', {
      type: 'warning'
    })
    await llmProvidersApi.delete(row.provider_key)
    ElMessage.success('删除成功')
    await fetchData()
  } catch (e: any) {
    if (e !== 'cancel') {
      ElMessage.error(messageFromError(e, '删除失败'))
    }
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
  gap: 16px;
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

.page-header p {
  margin: 6px 0 0;
  color: #6b7280;
  font-size: 14px;
}

.preset-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 16px;
  margin-bottom: 20px;
}

.preset-card {
  border-radius: 8px;
}

.preset-header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.preset-header h3 {
  margin: 0;
  font-size: 17px;
  color: #111827;
}

.preset-header p {
  margin: 6px 0 0;
  min-height: 40px;
  color: #6b7280;
  line-height: 1.5;
}

.preset-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.table-card {
  margin-bottom: 20px;
  border-radius: 8px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>

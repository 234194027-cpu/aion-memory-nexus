<template>
  <div class="memories-page">
    <el-card shadow="hover">
      <template #header>
        <div class="card-header">
          <span>已提交记忆</span>
          <el-tag type="success" size="large">{{ total }} 条记忆</el-tag>
        </div>
      </template>

      <!-- 搜索区域 -->
      <div class="search-area">
        <el-input
          v-model="searchQuery"
          placeholder="搜索记忆内容..."
          aria-label="搜索已提交记忆"
          :prefix-icon="Search"
          clearable
          style="width: 300px"
          @keyup.enter="handleSearch"
        />
        <el-button type="primary" :icon="Search" @click="handleSearch">
          搜索
        </el-button>
      </div>

      <!-- 记忆列表 -->
      <div class="table-scroll" tabindex="0" aria-label="已提交记忆表格，可横向滚动">
        <el-table
          :data="memories"
          v-loading="loading"
          stripe
        >
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
        <el-table-column prop="memory_type" label="类型" width="120">
          <template #default="{ row }">
            <el-tag :type="memoryTypeTagType(row.memory_type)">
              {{ memoryTypeLabel(row.memory_type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="body" label="内容" min-width="300" show-overflow-tooltip />
        <el-table-column prop="importance" label="重要性" width="100">
          <template #default="{ row }">
            <el-rate v-model="row.importance" disabled :max="5" />
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="180" />
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link :icon="View" @click="handleView(row)">
              查看
            </el-button>
            <el-button type="danger" link :icon="Delete" :loading="forgetting" :disabled="forgetting" @click="handleForget(row)">
              遗忘
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <LmEmptyState
            description="暂无记忆数据"
            action-text="开始对话"
            :action-icon="Collection"
            @action="goToConversation"
          />
        </template>
        </el-table>
      </div>

      <!-- 分页 -->
      <div v-if="total > 0" class="pagination">
        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[10, 20, 50, 100]"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="loadMemories"
          @current-change="loadMemories"
        />
      </div>
    </el-card>

    <!-- 记忆详情对话框 -->
    <el-dialog v-model="viewDialogVisible" title="记忆详情" width="700px">
      <el-descriptions :column="1" border v-if="currentMemory">
        <el-descriptions-item label="ID">{{ currentMemory.id }}</el-descriptions-item>
        <el-descriptions-item label="标题">{{ currentMemory.title }}</el-descriptions-item>
        <el-descriptions-item label="类型">
          <el-tag :type="memoryTypeTagType(currentMemory.memory_type)">
            {{ memoryTypeLabel(currentMemory.memory_type) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="重要性">
          <el-rate v-model="currentMemory.importance" disabled :max="5" />
        </el-descriptions-item>
        <el-descriptions-item label="内容">{{ currentMemory.body }}</el-descriptions-item>
        <el-descriptions-item label="标签">
          <el-tag v-for="tag in currentMemory.tags" :key="tag" size="small" style="margin-right: 8px">
            {{ tag }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="创建时间">{{ currentMemory.created_at }}</el-descriptions-item>
        <el-descriptions-item label="更新时间">{{ currentMemory.updated_at }}</el-descriptions-item>
      </el-descriptions>
    </el-dialog>

    <!-- 遗忘记忆对话框 -->
    <el-dialog v-model="forgetDialogVisible" title="遗忘记忆" width="520px">
      <el-form label-width="90px">
        <el-form-item label="遗忘动作">
          <el-radio-group v-model="forgetAction">
            <el-radio value="revoke">撤销</el-radio>
            <el-radio value="expire">过期</el-radio>
            <el-radio value="delete">删除</el-radio>
            <el-radio value="supersede">替代</el-radio>
          </el-radio-group>
        </el-form-item>
        <template v-if="forgetAction === 'supersede'">
          <el-form-item label="新标题" required>
            <el-input v-model="forgetNewTitle" placeholder="请输入新记忆标题" />
          </el-form-item>
          <el-form-item label="新内容" required>
            <el-input v-model="forgetNewBody" type="textarea" :rows="5" placeholder="请输入新记忆内容" />
          </el-form-item>
        </template>
        <div v-else style="color: #909399; font-size: 13px; line-height: 1.6; padding: 4px 0 0 90px;">
          <span v-if="forgetAction === 'revoke'">撤销：标记该记忆为失效，保留历史痕迹。</span>
          <span v-else-if="forgetAction === 'expire'">过期：标记该记忆为过期，不再参与检索。</span>
          <span v-else-if="forgetAction === 'delete'">删除：彻底从记忆库中移除该记忆。</span>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="forgetDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="forgetting" @click="submitForget">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { memoriesApi } from '../../api'
import { ElMessage } from 'element-plus'
import { Search, View, Delete, Collection } from '@element-plus/icons-vue'
import { memoryTypeLabel, memoryTypeTagType } from '../../utils/labels'
import LmEmptyState from '../../components/LmEmptyState.vue'

const router = useRouter()
const goToConversation = () => router.push('/advisor')

const memories = ref<any[]>([])
const loading = ref(false)
const searchQuery = ref('')
const currentPage = ref(1)
const pageSize = ref(10)
const total = ref(0)
const viewDialogVisible = ref(false)
const currentMemory = ref<any>(null)

// 遗忘记忆对话框状态
const forgetDialogVisible = ref(false)
const forgetting = ref(false)
const forgetAction = ref<'revoke' | 'expire' | 'delete' | 'supersede'>('revoke')
const forgetNewTitle = ref('')
const forgetNewBody = ref('')
const forgetTargetId = ref<string>('')

const loadMemories = async () => {
  loading.value = true
  try {
    // 后端已支持真分页：传 page + page_size，返回 { memories, total, page, page_size }
    const data = await memoriesApi.search({
      query: searchQuery.value,
      page: currentPage.value,
      page_size: pageSize.value
    })
    const list = data.memories || data.results || data.items || []
    total.value = data.total ?? (Array.isArray(list) ? list.length : 0)
    memories.value = Array.isArray(list) ? list : []
  } catch (error) {
    ElMessage.error('加载记忆失败')
  } finally {
    loading.value = false
  }
}

const handleSearch = () => {
  currentPage.value = 1
  loadMemories()
}

const handleView = (row: any) => {
  currentMemory.value = row
  viewDialogVisible.value = true
}

const handleForget = (row: any) => {
  // 打开对话框让用户选择遗忘动作
  forgetTargetId.value = row.id
  forgetAction.value = 'revoke'
  forgetNewTitle.value = ''
  forgetNewBody.value = ''
  forgetDialogVisible.value = true
}

const submitForget = async () => {
  // supersede 动作需要填写新标题和新内容
  if (forgetAction.value === 'supersede') {
    if (!forgetNewTitle.value.trim()) {
      ElMessage.warning('请输入新记忆标题')
      return
    }
    if (!forgetNewBody.value.trim()) {
      ElMessage.warning('请输入新记忆内容')
      return
    }
  }
  forgetting.value = true
  try {
    const payload: { action: string; new_title?: string; new_body?: string } = {
      action: forgetAction.value
    }
    if (forgetAction.value === 'supersede') {
      payload.new_title = forgetNewTitle.value.trim()
      payload.new_body = forgetNewBody.value.trim()
    }
    await memoriesApi.forget(forgetTargetId.value, payload)
    ElMessage.success('遗忘操作成功')
    forgetDialogVisible.value = false
    loadMemories()
  } catch (error: any) {
    ElMessage.error('遗忘失败：' + (error?.message || '未知错误'))
  } finally {
    forgetting.value = false
  }
}

onMounted(() => {
  loadMemories()
})
</script>

<style scoped>
.memories-page {
  max-width: 1600px;
  position: relative;
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
  font-size: 16px;
  color: #1a1a2e;
  position: relative;
  padding-bottom: 16px;
  border-bottom: 1px solid rgba(102, 126, 234, 0.1);
}

.card-header::after {
  content: '';
  position: absolute;
  bottom: -1px;
  left: 0;
  width: 60px;
  height: 2px;
  background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
  border-radius: 2px;
}

.search-area {
  margin-bottom: 24px;
  padding: 16px;
  background: linear-gradient(135deg, rgba(102, 126, 234, 0.03) 0%, rgba(118, 75, 162, 0.03) 100%);
  border-radius: 12px;
  border: 1px solid rgba(102, 126, 234, 0.08);
  display: flex;
  gap: 12px;
}

.table-scroll {
  width: 100%;
  overflow-x: auto;
}

.table-scroll:focus-visible {
  outline: 3px solid rgba(64, 158, 255, 0.65);
  outline-offset: 3px;
}

.table-scroll :deep(.el-table) {
  min-width: 1130px;
}

/* 输入框增强 */
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

:deep(.el-button--danger.is-link) {
  background: transparent;
  border: none;
}

:deep(.el-button--danger.is-link:hover) {
  color: #f56c6c;
  background: rgba(245, 108, 108, 0.1);
}

/* 分页增强 */
.pagination {
  margin-top: 24px;
  display: flex;
  justify-content: flex-end;
}

:deep(.el-pagination) {
  --el-pagination-button-bg-color: rgba(102, 126, 234, 0.05);
  --el-pagination-hover-color: #667eea;
}

:deep(.el-pagination .el-pager li) {
  border-radius: 8px;
  transition: all 0.3s ease;
}

:deep(.el-pagination .el-pager li:hover) {
  background: rgba(102, 126, 234, 0.15);
  color: #667eea;
}

:deep(.el-pagination .el-pager li.is-active) {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: #fff;
  box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3);
}

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

:deep(.el-descriptions) {
  border-radius: 12px;
  overflow: hidden;
}

:deep(.el-descriptions__label) {
  background: rgba(102, 126, 234, 0.05);
  font-weight: 500;
  color: #1a1a2e;
}

/* 评分组件 */
:deep(.el-rate) {
  height: auto;
}

:deep(.el-rate__icon) {
  font-size: 18px;
  transition: all 0.3s ease;
}

:deep(.el-rate__icon:hover) {
  transform: scale(1.2);
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

.memories-page {
  animation: fadeInUp 0.5s ease-out;
}
</style>

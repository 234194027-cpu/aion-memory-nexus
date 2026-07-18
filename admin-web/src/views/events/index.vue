<template>
  <div class="events-page">
    <el-card shadow="hover">
      <template #header>
        <div class="card-header">
          <span>事件管理</span>
          <el-button type="primary" :icon="Plus" @click="handleAdd">
            新增事件
          </el-button>
        </div>
      </template>

      <!-- 事件列表 -->
      <div class="table-scroll" tabindex="0" aria-label="事件表格，可横向滚动">
        <el-table
          :data="events"
          v-loading="loading"
          stripe
          @selection-change="handleSelectionChange"
        >
        <el-table-column type="selection" width="55" />
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="source_type" label="来源" width="120">
          <template #default="{ row }">
            <el-tag :type="getEventTypeColor(row.source_type)">
              {{ eventSourceLabel(row.source_type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="content" label="内容" min-width="300" show-overflow-tooltip />
        <el-table-column prop="processing_status" label="处理状态" width="120">
          <template #default="{ row }">
            {{ processingStatusLabel(row.processing_status) }}
          </template>
        </el-table-column>
        <el-table-column prop="occurred_at" label="发生时间" width="180" />
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link :icon="View" @click="handleView(row)">
              查看
            </el-button>
            <el-button type="danger" link :icon="Delete" :loading="deleting" :disabled="deleting" @click="handleDelete(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <LmEmptyState
            description="暂无事件数据"
            action-text="新增事件"
            :action-icon="Plus"
            @action="handleAdd"
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
          @size-change="handleSizeChange"
          @current-change="handleCurrentChange"
        />
      </div>
    </el-card>

    <!-- 事件详情对话框 -->
    <el-dialog v-model="viewDialogVisible" title="事件详情" width="600px">
      <el-descriptions :column="1" border v-if="currentEvent">
        <el-descriptions-item label="ID">{{ currentEvent.id }}</el-descriptions-item>
        <el-descriptions-item label="来源">
          <el-tag :type="getEventTypeColor(currentEvent.source_type)">
            {{ eventSourceLabel(currentEvent.source_type) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="内容">{{ currentEvent.content }}</el-descriptions-item>
        <el-descriptions-item label="处理状态">{{ processingStatusLabel(currentEvent.processing_status) }}</el-descriptions-item>
        <el-descriptions-item label="发生时间">{{ currentEvent.occurred_at }}</el-descriptions-item>
      </el-descriptions>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { eventsApi } from '../../api'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, View, Delete } from '@element-plus/icons-vue'
import { eventSourceLabel, processingStatusLabel } from '../../utils/labels'
import LmEmptyState from '../../components/LmEmptyState.vue'

const events = ref<any[]>([])
const loading = ref(false)
const deleting = ref(false)
const currentPage = ref(1)
const pageSize = ref(10)
const total = ref(0)
const viewDialogVisible = ref(false)
const currentEvent = ref<any>(null)
const selectedEvents = ref<any[]>([])

const getEventTypeColor = (type: string) => {
  const colorMap: Record<string, string> = {
    'manual': 'primary',
    'chat': 'success',
    'obsidian': 'warning',
    'agent_api': 'danger',
    'codex': 'info'
  }
  return colorMap[type] || 'info'
}

const loadEvents = async () => {
  loading.value = true
  try {
    // 后端已支持真分页：传 page + page_size，返回 { items, total, page, page_size }
    const data = await eventsApi.list({
      page: currentPage.value,
      page_size: pageSize.value
    })
    const list = Array.isArray(data) ? data : (data.items || [])
    total.value = data.total ?? list.length
    events.value = list
  } catch (error) {
    ElMessage.error('加载事件失败')
  } finally {
    loading.value = false
  }
}

const handleSizeChange = () => {
  currentPage.value = 1
  loadEvents()
}

const handleCurrentChange = () => {
  loadEvents()
}

const handleSelectionChange = (selection: any[]) => {
  selectedEvents.value = selection
}

const handleAdd = () => {
  ElMessage.info('新增功能开发中...')
}

const handleView = (row: any) => {
  currentEvent.value = row
  viewDialogVisible.value = true
}

const handleDelete = async (row: any) => {
  try {
    await ElMessageBox.confirm('确定要删除这个事件吗？工作 Agent 会同步清理仅由该事件支撑的案件和记忆来源。', '提示', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning'
    })
    deleting.value = true
    await eventsApi.delete(row.id)
    ElMessage.success('删除成功')
    loadEvents()
  } catch (error: any) {
    // 用户点击取消时 error === 'cancel'，无需报错
    if (error !== 'cancel' && error?.message !== 'cancel') {
      ElMessage.error('删除失败：' + (error?.message || '未知错误'))
    }
  } finally {
    deleting.value = false
  }
}

onMounted(() => {
  loadEvents()
})
</script>

<style scoped>
.events-page {
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

/* 搜索区域增强 */
.search-area {
  margin-bottom: 24px;
  padding: 16px;
  background: linear-gradient(135deg, rgba(102, 126, 234, 0.03) 0%, rgba(118, 75, 162, 0.03) 100%);
  border-radius: 12px;
  border: 1px solid rgba(102, 126, 234, 0.08);
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
  min-width: 1065px;
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

.events-page {
  animation: fadeInUp 0.5s ease-out;
}
</style>

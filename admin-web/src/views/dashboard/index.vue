<template>
  <div class="dashboard">
    <!-- 统计卡片 -->
    <el-row :gutter="20" class="stats-row">
      <el-col :xs="24" :sm="12" :lg="6" v-for="(stat, index) in stats" :key="index">
        <div class="stat-card" :style="{ background: stat.gradient }">
          <div class="stat-icon">{{ stat.icon }}</div>
          <div class="stat-content">
            <div class="stat-number">{{ stat.value }}</div>
            <div class="stat-label">{{ stat.label }}</div>
          </div>
        </div>
      </el-col>
    </el-row>

    <!-- 图表区域 -->
    <el-row :gutter="20" style="margin-top: 20px;">
      <el-col :xs="24" :lg="12">
        <el-card class="chart-card" shadow="hover">
          <template #header>
            <div class="card-header">
              <span>记忆类型分布</span>
            </div>
          </template>
          <div ref="typeChartRef" class="chart-container"></div>
        </el-card>
      </el-col>

      <el-col :xs="24" :lg="12">
        <el-card class="chart-card" shadow="hover">
          <template #header>
            <div class="card-header">
              <span>近7天记忆增长</span>
            </div>
          </template>
          <div ref="trendChartRef" class="chart-container"></div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 最近事件 -->
    <el-row :gutter="20" style="margin-top: 20px;">
      <el-col :xs="24">
        <el-card class="chart-card" shadow="hover">
          <template #header>
            <div class="card-header">
              <span>最近事件</span>
              <el-button type="primary" link @click="$router.push('/events')">
                查看全部
              </el-button>
            </div>
          </template>
          <el-table :data="recentEvents" stripe>
            <el-table-column prop="id" label="ID" width="80" />
            <el-table-column prop="content" label="内容" min-width="200" show-overflow-tooltip />
            <el-table-column prop="source_type" label="来源" width="120">
              <template #default="{ row }">
                <el-tag :type="getEventTypeColor(row.source_type)">
                  {{ eventSourceLabel(row.source_type) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="occurred_at" label="发生时间" width="180" />
          </el-table>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import { echarts, type ECharts } from '../../utils/echarts'
import { eventsApi, statsApi } from '../../api'
import { ElMessage } from 'element-plus'
import { eventSourceLabel } from '../../utils/labels'

const stats = ref([
  { icon: '📝', value: 0, label: '总事件', gradient: 'linear-gradient(145deg, #667eea, #764ba2)' },
  { icon: '💾', value: 0, label: '正式记忆', gradient: 'linear-gradient(145deg, #10b981, #059669)' },
  { icon: '✨', value: 0, label: '今日新增', gradient: 'linear-gradient(145deg, #8b5cf6, #7c3aed)' },
  { icon: '🤖', value: 0, label: '已接入 Agent', gradient: 'linear-gradient(145deg, #f59e0b, #d97706)' }
])

const recentEvents = ref([])
const typeChartRef = ref<HTMLElement>()
const trendChartRef = ref<HTMLElement>()

// 组件作用域持有 echarts 实例，便于卸载时 dispose 释放内存
let typeChartInstance: ECharts | null = null
let trendChartInstance: ECharts | null = null

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

const initCharts = () => {
  // 记忆类型分布饼图
  if (typeChartRef.value) {
    // 复用已有实例，避免重复 init 导致内存泄漏
    typeChartInstance = echarts.getInstanceByDom(typeChartRef.value) || echarts.init(typeChartRef.value)
    typeChartInstance.setOption({
      tooltip: { trigger: 'item' },
      legend: { bottom: '0%', left: 'center' },
      graphic: [{
        type: 'text',
        left: 'center',
        top: 'middle',
        style: { text: '暂无分类统计数据', fill: '#909399', fontSize: 14 }
      }],
      series: [{
        type: 'pie',
        radius: ['40%', '70%'],
        avoidLabelOverlap: false,
        itemStyle: { borderRadius: 10, borderColor: '#fff', borderWidth: 2 },
        label: { show: false },
        emphasis: { label: { show: true, fontSize: 14, fontWeight: 'bold' } },
        data: []
      }]
    })
  }

  // 近7天增长趋势图
  if (trendChartRef.value) {
    trendChartInstance = echarts.getInstanceByDom(trendChartRef.value) || echarts.init(trendChartRef.value)
    trendChartInstance.setOption({
      tooltip: { trigger: 'axis' },
      graphic: [{
        type: 'text',
        left: 'center',
        top: 'middle',
        style: { text: '暂无近 7 天增长数据', fill: '#909399', fontSize: 14 }
      }],
      grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
      },
      yAxis: { type: 'value' },
      series: [{
        name: '新增记忆',
        type: 'line',
        smooth: true,
        data: [],
        lineStyle: { color: '#667eea' },
        itemStyle: { color: '#667eea' },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: '#667eea' },
          { offset: 1, color: 'rgba(102, 126, 234, 0.1)' }
        ])}
      }]
    })
  }
}

const loadData = async () => {
  try {
    const [dashboardStats, eventsData] = await Promise.all([
      statsApi.getDashboardStats(),
      eventsApi.list({ limit: 10, ordering: '-created_at' })
    ])

    stats.value[0].value = dashboardStats.totalEvents
    stats.value[1].value = dashboardStats.totalMemories
    stats.value[2].value = dashboardStats.todayMemories
    stats.value[3].value = dashboardStats.totalAgents

    recentEvents.value = Array.isArray(eventsData) ? eventsData : (eventsData.items || [])
  } catch (error) {
    ElMessage.error('加载数据失败')
  }
}

// 窗口尺寸变化时重绘图表
const handleResize = () => {
  typeChartInstance?.resize()
  trendChartInstance?.resize()
}

onMounted(async () => {
  await loadData()
  await nextTick()
  initCharts()
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  // 释放 echarts 实例，避免路由切换导致的内存泄漏
  typeChartInstance?.dispose()
  trendChartInstance?.dispose()
  typeChartInstance = null
  trendChartInstance = null
  window.removeEventListener('resize', handleResize)
})
</script>

<style scoped>
.dashboard {
  max-width: 1600px;
  position: relative;
  z-index: 1;
}

.stats-row {
  margin-bottom: 10px;
}

/* 增强统计卡片 - 多层阴影 + 悬浮效果 */
.stat-card {
  border-radius: 16px;
  padding: 24px;
  color: #fff;
  display: flex;
  align-items: center;
  gap: 16px;
  position: relative;
  overflow: hidden;
  /* 多层阴影营造深度 */
  box-shadow:
    0 2px 4px rgba(0, 0, 0, 0.02),
    0 8px 16px rgba(0, 0, 0, 0.06),
    0 16px 32px rgba(0, 0, 0, 0.08);
  transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
}

/* 卡片光效装饰 */
.stat-card::before {
  content: '';
  position: absolute;
  top: 0;
  right: 0;
  width: 120px;
  height: 120px;
  background: radial-gradient(circle, rgba(255, 255, 255, 0.15) 0%, transparent 70%);
  transform: translate(30%, -30%);
}

.stat-card::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, transparent 0%, rgba(255, 255, 255, 0.4) 50%, transparent 100%);
}

.stat-card:hover {
  transform: translateY(-8px) scale(1.02);
  box-shadow:
    0 4px 8px rgba(0, 0, 0, 0.04),
    0 12px 24px rgba(0, 0, 0, 0.08),
    0 24px 48px rgba(0, 0, 0, 0.12),
    0 0 40px rgba(102, 126, 234, 0.2);
}

/* 卡片悬浮时的光效 */
.stat-card:hover::before {
  width: 150px;
  height: 150px;
  transform: translate(20%, -40%);
  transition: all 0.4s ease;
}

.stat-icon {
  font-size: 40px;
  filter: drop-shadow(0 2px 4px rgba(0, 0, 0, 0.2));
  position: relative;
  z-index: 1;
}

.stat-number {
  font-size: 32px;
  font-weight: 700;
  text-shadow: 0 2px 4px rgba(0, 0, 0, 0.15);
  position: relative;
  z-index: 1;
}

.stat-label {
  font-size: 14px;
  opacity: 0.95;
  margin-top: 6px;
  font-weight: 500;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.1);
  position: relative;
  z-index: 1;
}

/* 图表卡片增强 */
.chart-card {
  border-radius: 16px;
  border: none;
  /* 玻璃拟态效果 + 多层阴影 */
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

/* 卡片顶部装饰 */
.chart-card::before {
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

.chart-card:hover {
  transform: translateY(-4px);
  box-shadow:
    0 4px 12px rgba(0, 0, 0, 0.04),
    0 12px 32px rgba(0, 0, 0, 0.06),
    0 24px 64px rgba(0, 0, 0, 0.04),
    0 0 30px rgba(102, 126, 234, 0.1);
}

.chart-card:hover::before {
  opacity: 1;
}

/* 卡片内部阴影 */
:deep(.el-card__body) {
  padding: 20px;
  position: relative;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: 600;
  color: #1a1a2e;
  font-size: 15px;
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

.chart-container {
  height: 300px;
}

/* 表格样式增强 */
:deep(.el-table) {
  --el-table-border-color: rgba(102, 126, 234, 0.1);
  --el-table-header-bg-color: rgba(102, 126, 234, 0.03);
  border-radius: 12px;
  overflow: hidden;
}

:deep(.el-table th.el-table__cell) {
  background: linear-gradient(135deg, rgba(102, 126, 234, 0.05) 0%, rgba(118, 75, 162, 0.05) 100%);
  font-weight: 600;
  color: #1a1a2e;
}

:deep(.el-table tr) {
  transition: all 0.3s ease;
}

:deep(.el-table tr:hover > td.el-table__cell) {
  background: linear-gradient(135deg, rgba(102, 126, 234, 0.05) 0%, rgba(118, 75, 162, 0.03) 100%) !important;
}

:deep(.el-table--striped .el-table__body tr.el-table__row--striped td.el-table__cell) {
  background: rgba(102, 126, 234, 0.02);
}

/* 标签样式增强 */
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

@media (max-width: 768px) {
  .stat-card {
    padding: 20px;
  }

  .stat-icon {
    font-size: 32px;
  }

  .stat-number {
    font-size: 24px;
  }

  .chart-card:hover {
    transform: translateY(-2px);
  }
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

.dashboard {
  animation: fadeInUp 0.5s ease-out;
}

/* 统计卡片交错动画 */
.stats-row .el-col {
  animation: fadeInUp 0.5s ease-out backwards;
}

.stats-row .el-col:nth-child(1) { animation-delay: 0.1s; }
.stats-row .el-col:nth-child(2) { animation-delay: 0.2s; }
.stats-row .el-col:nth-child(3) { animation-delay: 0.3s; }
.stats-row .el-col:nth-child(4) { animation-delay: 0.4s; }
</style>

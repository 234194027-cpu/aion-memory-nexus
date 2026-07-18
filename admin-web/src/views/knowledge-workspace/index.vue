<template>
  <main class="knowledge-workspace" aria-labelledby="knowledge-workspace-title">
    <section class="workspace-hero">
      <div>
        <p class="eyebrow">PERSONAL KNOWLEDGE WORKSPACE</p>
        <h1 id="knowledge-workspace-title">知识工作区</h1>
        <p>从原始记忆中回看关系、发生时间与可追溯的主题知识。</p>
      </div>
      <el-button :loading="refreshing" type="primary" @click="refreshAll">
        刷新视图
      </el-button>
    </section>

    <el-alert
      class="provenance-notice"
      title="这里展示的是已提交记忆及其已有关系；Wiki 仅按现有标签自动聚合，不会覆盖原始内容。"
      type="info"
      :closable="false"
      show-icon
    />

    <el-tabs v-model="activeTab" class="workspace-tabs" aria-label="知识工作区视图">
      <el-tab-pane label="关系图谱" name="graph">
        <section class="view-card">
          <div class="view-heading">
            <div>
              <h2>节点与关系</h2>
              <p>点击节点查看记忆详情；拖动画布浏览关联。</p>
            </div>
            <span class="count-pill">{{ graphData.nodes.length }} 条记忆 · {{ graphData.edges.length }} 条关系</span>
          </div>
          <div class="graph-controls" aria-label="关系图谱筛选">
            <el-select v-model="graphLimit" size="small" aria-label="图谱密度" @change="loadGraph">
              <el-option label="最近 80 条记忆" :value="80" />
              <el-option label="最近 120 条记忆" :value="120" />
              <el-option label="最多 200 条记忆" :value="200" />
            </el-select>
            <el-select v-model="relationFilter" clearable placeholder="全部关系类型" size="small" aria-label="关系类型" @change="initGraph">
              <el-option v-for="type in relationTypes" :key="type" :label="type" :value="type" />
            </el-select>
          </div>
          <el-skeleton v-if="loading.graph" :rows="8" animated />
          <el-empty v-else-if="!graphData.nodes.length" description="暂无可视化关系。先提交记忆并建立关系后，这里会出现图谱。" />
          <template v-else>
            <div ref="graphRef" class="graph-canvas" role="img" aria-label="记忆关系图谱；下方提供可访问的关系列表" />
            <section class="relation-list" aria-labelledby="relation-list-title">
              <h3 id="relation-list-title">关系清单</h3>
              <p>这是图谱的键盘与读屏替代视图。关系只来自已保存的边，不由页面推测生成。</p>
              <el-empty v-if="!filteredEdges.length" :image-size="56" description="当前筛选没有关系" />
              <button v-for="edge in filteredEdges" :key="edge.id" class="relation-row" type="button" @click="openMemory(edge.source)">
                <span><strong>{{ nodeTitle(edge.source) }}</strong> → <strong>{{ nodeTitle(edge.target) }}</strong></span>
                <small>{{ edge.relation_type }} · {{ confidencePercent(edge.confidence) }}{{ edge.reason ? ` · ${edge.reason}` : '' }}</small>
                <small v-if="edge.valid_from || edge.valid_until">有效期：{{ formatDate(edge.valid_from) }} ～ {{ formatDate(edge.valid_until) }}</small>
              </button>
            </section>
          </template>
        </section>
      </el-tab-pane>

      <el-tab-pane label="人生时间线" name="timeline">
        <section class="view-card">
          <div class="view-heading">
            <div>
              <h2>按发生时间回顾</h2>
              <p>优先使用记忆发生时间；缺失时以记录时间补位并明确标注。</p>
            </div>
            <span class="count-pill">{{ timeline.length }} 条记忆</span>
          </div>
          <el-skeleton v-if="loading.timeline" :rows="7" animated />
          <el-empty v-else-if="!timeline.length" description="暂无可放入时间线的已提交记忆。" />
          <el-timeline v-else class="life-timeline">
            <el-timeline-item v-for="entry in timeline" :key="entry.memory_id" :timestamp="formatDate(entry.occurred_at)" placement="top" type="primary">
              <article class="timeline-item" tabindex="0" @click="openMemory(entry.memory_id)" @keyup.enter="openMemory(entry.memory_id)">
                <div class="timeline-item-title">{{ entry.title }}</div>
                <div class="timeline-meta">
                  <el-tag size="small" effect="plain">{{ memoryTypeLabel(entry.memory_type) }}</el-tag>
                  <el-tag v-if="entry.epistemic_status" size="small" type="info">{{ epistemicLabel(entry.epistemic_status) }}</el-tag>
                  <span>{{ entry.time_basis === 'occurred_at' ? '记忆发生时间' : '记录时间（发生时间缺失）' }}</span>
                </div>
                <div v-if="entry.tags?.length" class="tag-list">
                  <el-tag v-for="tag in entry.tags.slice(0, 4)" :key="tag" size="small" type="info">{{ tag }}</el-tag>
                </div>
              </article>
            </el-timeline-item>
          </el-timeline>
        </section>
      </el-tab-pane>

      <el-tab-pane label="Wiki 知识页" name="wiki">
        <section class="view-card">
          <div class="view-heading wiki-heading">
            <div>
              <h2>自动聚合的主题知识</h2>
              <p>每一页保留关联记忆与原始来源，主题之间用共享记忆建立双向链接。</p>
            </div>
            <el-button :loading="rebuilding" plain @click="rebuildWiki">重新聚合</el-button>
          </div>
          <el-skeleton v-if="loading.wiki" :rows="7" animated />
          <el-empty v-else-if="!wikiPages.length" description="暂无可聚合的标签主题。为已提交记忆添加标签后可生成 Wiki 页面。" />
          <div v-else class="wiki-layout">
            <div class="wiki-list" aria-label="Wiki 页面列表">
              <button
                v-for="page in wikiPages"
                :key="page.slug"
                class="wiki-card"
                :class="{ active: selectedPage?.slug === page.slug }"
                type="button"
                @click="selectWikiPage(page.slug)"
              >
                <span class="wiki-card-title">{{ page.title }}</span>
                <span class="wiki-card-summary">{{ page.summary }}</span>
                <span class="wiki-card-meta">{{ page.source_count }} 条来源 · 置信度 {{ confidencePercent(page.confidence) }} · {{ confidenceLabel(page.confidence_state) }}</span>
              </button>
            </div>
            <article v-if="selectedPage" class="wiki-detail" aria-live="polite">
              <el-skeleton v-if="loading.wikiDetail" :rows="8" animated />
              <template v-else>
                <p class="eyebrow">KNOWLEDGE PAGE</p>
                <h3>{{ selectedPage.title }}</h3>
                <p class="wiki-summary">{{ selectedPage.summary }}</p>
                <div class="detail-stat-row">
                  <span>置信度 {{ confidencePercent(selectedPage.confidence) }}</span>
                  <span>{{ selectedPage.source_count }} 条可追溯来源</span>
                  <span>本次变化：{{ changeReasonLabel(selectedPage.last_change_reason) }}</span>
                </div>
                <section v-if="selectedPage.related_slugs.length" class="related-section">
                  <h4>关联概念</h4>
                  <div class="tag-list">
                    <el-button v-for="slug in selectedPage.related_slugs" :key="slug" size="small" text type="primary" @click="selectWikiPage(slug)">
                      ↔ {{ wikiTitle(slug) }}
                    </el-button>
                  </div>
                </section>
                <section class="related-section">
                  <h4>相关人生经历</h4>
                  <el-empty v-if="!selectedPage.memories?.length" :image-size="72" description="暂无关联记忆" />
                  <button v-for="memory in selectedPage.memories || []" :key="memory.id" class="memory-link" type="button" @click="openMemory(memory.id)">
                    <span>{{ memory.title }}<small> · {{ memory.relation_basis === 'tag' ? '现有标签关联' : memory.relation_basis }}</small></span><small>{{ formatDate(memory.occurred_at) }} · {{ confidenceLabel(memory.confidence_state) }}</small>
                  </button>
                </section>
                <section class="related-section">
                  <h4>原始来源</h4>
                  <p class="source-count">{{ selectedPage.source_refs?.length || 0 }} 条来源记录可供回溯；原始输入不会被本页改写。</p>
                </section>
                <section v-if="selectedPage.version_history?.length" class="related-section">
                  <h4>知识演化</h4>
                  <ol class="version-list">
                    <li v-for="version in selectedPage.version_history" :key="`${version.generated_at}-${version.change_reason}`">
                      <span>{{ changeReasonLabel(version.change_reason) }}</span>
                      <small>{{ formatDate(version.generated_at) }} · {{ version.memory_count }} 条记忆 · {{ confidencePercent(version.confidence) }}</small>
                    </li>
                  </ol>
                </section>
              </template>
            </article>
          </div>
        </section>
      </el-tab-pane>
    </el-tabs>

    <el-drawer v-model="memoryDrawerOpen" title="记忆详情" direction="rtl" size="min(420px, 100vw)">
      <el-skeleton v-if="loading.memory" :rows="10" animated />
      <el-empty v-else-if="!selectedMemory" description="无法读取这条记忆。" />
      <template v-else>
        <h2 class="drawer-title">{{ selectedMemory.title }}</h2>
        <el-tag size="small">{{ memoryTypeLabel(selectedMemory.memory_type) }}</el-tag>
        <p class="memory-body">{{ selectedMemory.body }}</p>
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="发生时间">{{ formatDate(selectedMemory.valid_from || selectedMemory.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="置信度">{{ confidencePercent(selectedMemory.confidence) }}</el-descriptions-item>
          <el-descriptions-item label="来源">{{ selectedMemory.sources?.length || 0 }} 条</el-descriptions-item>
        </el-descriptions>
      </template>
    </el-drawer>
  </main>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { echarts, type ECharts } from '../../utils/echarts'
import { ElMessage } from 'element-plus'
import {
  knowledgeWorkspaceApi,
  memoriesApi,
  type KnowledgeGraphNode,
  type KnowledgeTimelineEntry,
  type WikiPage,
} from '../../api'
import { memoryTypeLabel } from '../../utils/labels'

const activeTab = ref('graph')
const graphRef = ref<HTMLElement>()
const graphData = ref<{ nodes: KnowledgeGraphNode[]; edges: any[] }>({ nodes: [], edges: [] })
const graphLimit = ref(120)
const relationFilter = ref('')
const timeline = ref<KnowledgeTimelineEntry[]>([])
const wikiPages = ref<WikiPage[]>([])
const selectedPage = ref<WikiPage | null>(null)
const selectedMemory = ref<any>(null)
const memoryDrawerOpen = ref(false)
const refreshing = ref(false)
const rebuilding = ref(false)
const loading = ref({ graph: false, timeline: false, wiki: false, wikiDetail: false, memory: false })
let graphInstance: ECharts | null = null

const formatDate = (value?: string | null) => value ? new Date(value).toLocaleString('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }) : '未记录'
const confidencePercent = (value: number) => `${Math.round((value || 0) * 100)}%`
const wikiTitle = (slug: string) => wikiPages.value.find(page => page.slug === slug)?.title || slug
const relationTypes = computed(() => [...new Set(graphData.value.edges.map(edge => edge.relation_type))].sort())
const filteredEdges = computed(() => graphData.value.edges.filter(edge => !relationFilter.value || edge.relation_type === relationFilter.value))
const nodeTitle = (id: string) => graphData.value.nodes.find(node => node.id === id)?.title || id
const confidenceLabel = (state?: string) => ({ low: '低置信', review: '待复核', supported: '证据充分' })[state || ''] || '未标记'
const epistemicLabel = (value: string) => ({ user_assertion: '用户陈述', user_confirmed: '用户确认', user_imported: '用户导入', agent_assertion: 'Agent 陈述', assistant_supplied: '助手提供', model_inference: '模型推断', legacy_unclassified: '历史未分类' })[value] || '未分类'
const changeReasonLabel = (value?: string | null) => ({ initial_aggregation: '首次聚合', membership_changed: '关联记忆变化', derived_summary_changed: '摘要或置信度变化', no_active_members: '没有可用记忆' })[value || ''] || '尚无变更记录'

const initGraph = async () => {
  await nextTick()
  if (!graphRef.value || !graphData.value.nodes.length) return
  graphInstance = echarts.getInstanceByDom(graphRef.value) || echarts.init(graphRef.value)
  graphInstance.setOption({
    animationDurationUpdate: 280,
    tooltip: { formatter: (item: any) => item.dataType === 'edge' ? `${item.data.relation_type} · ${confidencePercent(item.data.confidence)}` : item.data.name },
    series: [{
      type: 'graph', layout: 'force', roam: true, draggable: true, cursor: 'pointer',
      force: { repulsion: 220, edgeLength: [90, 170], gravity: 0.08 },
      label: { show: true, position: 'right', color: '#243048', fontSize: 12, formatter: (item: any) => item.data.name.length > 14 ? `${item.data.name.slice(0, 14)}…` : item.data.name },
      lineStyle: { color: '#9eacc5', curveness: 0.12, opacity: 0.72 },
      data: graphData.value.nodes.map((node, index) => ({
        ...node, name: node.title, value: node.id, symbolSize: 26 + Math.round(node.importance * 18),
        itemStyle: { color: ['#5f6fff', '#11a37f', '#e18b45', '#845ec2'][index % 4] },
      })),
      links: filteredEdges.value.map(edge => ({ ...edge, source: edge.source, target: edge.target, label: { show: true, formatter: edge.relation_type, color: '#667085', fontSize: 10 } })),
    }],
  }, true)
  graphInstance.off('click')
  graphInstance.on('click', (params: any) => {
    if (params.dataType === 'node') openMemory(params.data.id)
  })
}

const loadGraph = async () => {
  loading.value.graph = true
  try {
    const data = await knowledgeWorkspaceApi.graph(graphLimit.value)
    graphData.value = { nodes: data.nodes || [], edges: data.edges || [] }
    await initGraph()
  } finally { loading.value.graph = false }
}

const loadTimeline = async () => {
  loading.value.timeline = true
  try { timeline.value = (await knowledgeWorkspaceApi.timeline()).entries || [] } finally { loading.value.timeline = false }
}

const loadWiki = async (autoSelect = true) => {
  loading.value.wiki = true
  try {
    wikiPages.value = await knowledgeWorkspaceApi.wiki() || []
    if (autoSelect && wikiPages.value.length) await selectWikiPage(wikiPages.value[0].slug)
    if (!wikiPages.value.length) selectedPage.value = null
  } finally { loading.value.wiki = false }
}

const selectWikiPage = async (slug: string) => {
  loading.value.wikiDetail = true
  try { selectedPage.value = await knowledgeWorkspaceApi.wikiPage(slug) } finally { loading.value.wikiDetail = false }
}

const rebuildWiki = async () => {
  rebuilding.value = true
  try {
    await knowledgeWorkspaceApi.rebuildWiki()
    await loadWiki()
    ElMessage.success('Wiki 已按现有标签重新聚合')
  } finally { rebuilding.value = false }
}

const openMemory = async (memoryId: string) => {
  memoryDrawerOpen.value = true
  loading.value.memory = true
  selectedMemory.value = null
  try { selectedMemory.value = await memoriesApi.get(memoryId) } catch { memoryDrawerOpen.value = false } finally { loading.value.memory = false }
}

const refreshAll = async () => {
  refreshing.value = true
  try { await Promise.all([loadGraph(), loadTimeline(), loadWiki(false)]) } catch { ElMessage.error('知识工作区加载失败') } finally { refreshing.value = false }
}

const resizeGraph = () => graphInstance?.resize()
watch(activeTab, async tab => { if (tab === 'graph') await initGraph() })
onMounted(async () => { await refreshAll(); window.addEventListener('resize', resizeGraph) })
onUnmounted(() => { graphInstance?.dispose(); graphInstance = null; window.removeEventListener('resize', resizeGraph) })
</script>

<style scoped>
.knowledge-workspace { max-width: 1440px; color: #213047; }
.workspace-hero { display: flex; align-items: end; justify-content: space-between; gap: 24px; margin-bottom: 18px; }
.workspace-hero h1 { margin: 2px 0 8px; font-size: clamp(28px, 4vw, 38px); letter-spacing: -0.04em; }
.workspace-hero p { margin: 0; color: #64748b; }
.eyebrow { margin: 0; color: #5f6fff; font-size: 11px; font-weight: 800; letter-spacing: 0.12em; }
.provenance-notice { margin-bottom: 18px; }
.workspace-tabs :deep(.el-tabs__header) { margin-bottom: 18px; }
.view-card { min-height: 530px; padding: 26px; border: 1px solid #e8edf5; border-radius: 20px; background: #fff; box-shadow: 0 12px 38px rgba(39, 61, 94, 0.06); }
.view-heading { display: flex; justify-content: space-between; gap: 20px; align-items: start; margin-bottom: 22px; }
.view-heading h2, .wiki-detail h3 { margin: 0 0 6px; font-size: 21px; letter-spacing: -0.025em; }
.view-heading p, .wiki-summary, .source-count { margin: 0; color: #667085; line-height: 1.65; }
.count-pill { flex: none; border-radius: 999px; background: #eef2ff; color: #4b59d4; padding: 6px 10px; font-size: 12px; font-weight: 700; }
.graph-canvas { width: 100%; height: 440px; outline: none; }
.graph-controls { display: flex; flex-wrap: wrap; gap: 10px; margin: -4px 0 14px; }
.graph-controls :deep(.el-select) { width: min(220px, 100%); }
.relation-list { margin-top: 18px; border-top: 1px solid #e8edf5; padding-top: 16px; }
.relation-list h3 { margin: 0 0 4px; font-size: 15px; }.relation-list > p { margin: 0 0 10px; color: #667085; font-size: 13px; }
.relation-row { display: grid; gap: 3px; width: 100%; border: 0; border-top: 1px solid #eef1f6; background: transparent; padding: 10px 0; color: #334155; cursor: pointer; font: inherit; text-align: left; }.relation-row:hover, .relation-row:focus-visible { color: #4f5fe5; outline: none; }.relation-row small { color: #748197; }
.life-timeline { max-width: 900px; padding: 8px 4px 0; }
.timeline-item { cursor: pointer; border: 1px solid #e8edf5; border-radius: 14px; padding: 14px 16px; background: #fbfcff; transition: border-color .2s ease, transform .2s ease; }
.timeline-item:hover, .timeline-item:focus-visible { border-color: #8793ff; transform: translateX(3px); outline: none; }
.timeline-item-title { font-weight: 750; margin-bottom: 8px; }
.timeline-meta, .detail-stat-row { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px; color: #667085; font-size: 13px; }
.tag-list { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.wiki-heading { align-items: center; }
.wiki-layout { display: grid; grid-template-columns: minmax(230px, 0.8fr) minmax(0, 1.45fr); gap: 22px; }
.wiki-list { display: grid; align-content: start; gap: 10px; max-height: 510px; overflow: auto; padding-right: 5px; }
.wiki-card { width: 100%; text-align: left; cursor: pointer; border: 1px solid #e6ebf3; border-radius: 14px; padding: 14px; background: #fff; color: inherit; transition: border-color .2s ease, background .2s ease; }
.wiki-card:hover, .wiki-card:focus-visible, .wiki-card.active { border-color: #7d89ff; background: #f5f6ff; outline: none; }
.wiki-card-title, .wiki-card-summary, .wiki-card-meta { display: block; }
.wiki-card-title { font-weight: 760; margin-bottom: 5px; }.wiki-card-summary { color: #667085; font-size: 13px; line-height: 1.45; }.wiki-card-meta { margin-top: 10px; color: #8490a3; font-size: 12px; }
.wiki-detail { min-height: 390px; border-radius: 16px; padding: 23px; background: linear-gradient(145deg, #f8f9ff, #fff); border: 1px solid #edf0fb; }
.wiki-summary { font-size: 16px; margin: 10px 0 14px; }.related-section { margin-top: 24px; }.related-section h4 { margin: 0 0 9px; font-size: 14px; }.memory-link { display: flex; justify-content: space-between; width: 100%; border: 0; border-top: 1px solid #e8edf5; background: transparent; cursor: pointer; text-align: left; padding: 11px 0; color: #35425b; font: inherit; }.memory-link:hover { color: #4f5fe5; }.memory-link small { color: #8490a3; }.drawer-title { font-size: 21px; }.memory-body { white-space: pre-wrap; line-height: 1.7; color: #475569; }
.version-list { display: grid; gap: 8px; margin: 0; padding-left: 18px; }.version-list li { display: grid; gap: 2px; color: #475569; font-size: 13px; }.version-list small { color: #8490a3; }
@media (max-width: 720px) { .workspace-hero, .view-heading { align-items: stretch; flex-direction: column; }.view-card { min-height: 470px; padding: 18px; border-radius: 16px; }.graph-canvas { height: 380px; }.wiki-layout { grid-template-columns: 1fr; }.wiki-list { max-height: 260px; }.wiki-detail { padding: 18px; }.count-pill { align-self: flex-start; } }
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; scroll-behavior: auto !important; } }
</style>

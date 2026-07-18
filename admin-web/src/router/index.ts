import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    component: () => import('../views/layout/MainLayout.vue'),
    redirect: '/daily',
    children: [
      // 仪表板
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('../views/dashboard/index.vue'),
        meta: { title: '仪表板', icon: 'DataAnalysis' }
      },
      // 记忆管理
      {
        path: 'events',
        name: 'Events',
        component: () => import('../views/events/index.vue'),
        meta: { title: '事件采集', icon: 'Calendar' }
      },
      {
        path: 'memories',
        name: 'Memories',
        component: () => import('../views/memories/index.vue'),
        meta: { title: '已提交记忆', icon: 'Document' }
      },
      // 认知引擎
      {
        path: 'persona',
        name: 'Persona',
        component: () => import('../views/persona/index.vue'),
        meta: { title: '人物画像', icon: 'User' }
      },
      {
        path: 'governance',
        name: 'Governance',
        component: () => import('../views/governance/index.vue'),
        meta: { title: '记忆治理', icon: 'ScaleToOriginal' }
      },
      {
        path: 'advisor',
        name: 'Advisor',
        component: () => import('../views/advisor/index.vue'),
        meta: { title: '认知顾问', icon: 'ChatDotRound' }
      },
      {
        path: 'daily',
        name: 'Daily',
        component: () => import('../views/daily/index.vue'),
        meta: { title: '每日简报', icon: 'Memo' }
      },
      {
        path: 'knowledge-workspace',
        name: 'KnowledgeWorkspace',
        component: () => import('../views/knowledge-workspace/index.vue'),
        meta: { title: '知识工作区', icon: 'Share' }
      },
      // 执行系统
      {
        path: 'agents',
        name: 'Agents',
        component: () => import('../views/agents/index.vue'),
        meta: { title: 'Agent 管理', icon: 'Avatar' }
      },
      {
        path: 'orchestration',
        name: 'Orchestration',
        component: () => import('../views/orchestration/index.vue'),
        meta: { title: '任务编排', icon: 'Connection' }
      },
      // 系统设置
      {
        path: 'settings',
        name: 'Settings',
        component: () => import('../views/settings/index.vue'),
        meta: { title: '设置', icon: 'Setting' }
      },
      {
        path: 'obsidian',
        name: 'Obsidian',
        component: () => import('../views/obsidian/index.vue'),
        meta: { title: 'Obsidian 同步', icon: 'FolderOpened' }
      },
      {
        path: 'llm-providers',
        name: 'LLMProviders',
        component: () => import('../views/llm-providers/index.vue'),
        meta: { title: 'LLM 提供商', icon: 'Cloudy' }
      },
      {
        path: 'wecom',
        name: 'WeCom',
        component: () => import('../views/wecom/index.vue'),
        meta: { title: '企业微信', icon: 'Message' }
      },
      {
        path: 'about',
        name: 'About',
        component: () => import('../views/about/index.vue'),
        meta: { title: '关于与版本', icon: 'InfoFilled' }
      },
      {
        // 旧深链兼容（白皮书 18.7 节第 15 项）：/system-info 重定向到 /about
        path: 'system-info',
        redirect: '/about'
      }
    ]
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/daily'
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router

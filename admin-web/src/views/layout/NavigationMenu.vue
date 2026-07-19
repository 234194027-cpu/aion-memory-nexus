<template>
  <el-menu
    :default-active="route.path"
    :collapse="collapsed"
    router
    class="sidebar-menu"
    background-color="#ffffff"
    text-color="#606266"
    active-text-color="#409eff"
    @select="emit('navigate')"
  >
    <el-menu-item index="/dashboard">
      <el-icon><DataAnalysis /></el-icon>
      <template #title>首页</template>
    </el-menu-item>

    <el-sub-menu index="today">
      <template #title><el-icon><Memo /></el-icon><span>今日</span></template>
      <el-menu-item index="/daily">每日简报</el-menu-item>
      <el-menu-item index="/advisor">认知顾问</el-menu-item>
    </el-sub-menu>

    <el-sub-menu index="memory">
      <template #title><el-icon><Collection /></el-icon><span>记忆</span></template>
      <el-menu-item index="/memories">正式记忆</el-menu-item>
      <el-menu-item index="/events">事件与来源</el-menu-item>
      <el-menu-item index="/persona">人物画像</el-menu-item>
      <el-menu-item index="/governance">治理审计</el-menu-item>
      <el-menu-item index="/knowledge-workspace">知识工作区</el-menu-item>
    </el-sub-menu>

    <el-sub-menu index="agent">
      <template #title><el-icon><Avatar /></el-icon><span>Agent</span></template>
      <el-menu-item index="/agents">双 Agent Runtime</el-menu-item>
      <el-menu-item index="/orchestration">任务编排</el-menu-item>
    </el-sub-menu>

    <el-sub-menu index="settings">
      <template #title><el-icon><Setting /></el-icon><span>设置</span></template>
      <el-menu-item index="/settings">通用与数据权利</el-menu-item>
      <el-menu-item index="/wecom">企业微信</el-menu-item>
      <el-menu-item index="/obsidian">Obsidian</el-menu-item>
      <el-menu-item index="/llm-providers">模型提供商</el-menu-item>
      <el-menu-item index="/about">项目版本</el-menu-item>
    </el-sub-menu>
  </el-menu>
</template>

<script setup lang="ts">
import { useRoute } from 'vue-router'
import { Avatar, Collection, DataAnalysis, Memo, Setting } from '@element-plus/icons-vue'

defineProps<{ collapsed: boolean }>()
const emit = defineEmits<{ navigate: [] }>()
const route = useRoute()
</script>

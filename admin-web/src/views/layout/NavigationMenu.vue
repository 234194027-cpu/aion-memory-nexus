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
    <el-menu-item index="/daily">
      <el-icon><Memo /></el-icon>
      <template #title>今天</template>
    </el-menu-item>

    <el-menu-item index="/advisor">
      <el-icon><ChatDotRound /></el-icon>
      <template #title>对话</template>
    </el-menu-item>

    <el-sub-menu index="/memory">
      <template #title>
        <el-icon><Collection /></el-icon>
        <span>记忆</span>
      </template>
      <el-menu-item index="/memories"><el-icon><Document /></el-icon><template #title>正式记忆</template></el-menu-item>
    </el-sub-menu>

    <el-sub-menu index="/knowledge">
      <template #title><el-icon><Share /></el-icon><span>人生知识</span></template>
      <el-menu-item index="/persona"><el-icon><User /></el-icon><template #title>认识我</template></el-menu-item>
      <el-menu-item index="/knowledge-workspace"><el-icon><Share /></el-icon><template #title>图谱与时间线</template></el-menu-item>
    </el-sub-menu>

    <el-menu-item index="/settings">
      <el-icon><Setting /></el-icon>
      <template #title>设置</template>
    </el-menu-item>
  </el-menu>
</template>

<script setup lang="ts">
import { useRoute } from 'vue-router'
import { Memo, Document, User, Collection, ChatDotRound, Setting, Share } from '@element-plus/icons-vue'

defineProps<{ collapsed: boolean }>()
const emit = defineEmits<{ navigate: [] }>()
const route = useRoute()
</script>

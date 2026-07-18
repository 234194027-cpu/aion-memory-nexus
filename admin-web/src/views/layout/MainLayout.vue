<template>
  <el-container class="main-layout">
    <!-- 侧边栏：桌面端固定显示，移动端完全隐藏改为抽屉模式（T08） -->
    <el-aside
      v-show="!isMobile"
      :width="sidebarWidth"
      class="sidebar"
      :class="{ 'sidebar-mobile': isMobile }"
    >
      <div class="logo">
        <el-icon v-if="sidebarCollapsed" :size="24"><Memo /></el-icon>
        <span v-else class="logo-text">Aion Memory Nexus</span>
      </div>
      <NavigationMenu :collapsed="sidebarCollapsed" @navigate="closeMobileNavigation" />
    </el-aside>

    <!-- 主体区域 -->
    <el-container>
      <!-- 顶部导航 -->
      <el-header class="header">
        <div class="header-left">
          <el-button class="icon-button" text circle :aria-label="navigationButtonLabel" @click="toggleNavigation">
            <el-icon><Fold v-if="!sidebarCollapsed" /><Expand v-else /></el-icon>
          </el-button>
        </div>

        <div class="header-right">
          <el-tooltip content="切换主题">
            <el-button class="icon-button" text circle aria-label="切换主题" @click="store.toggleTheme">
              <el-icon><Sunny v-if="store.theme === 'dark'" /><Moon v-else /></el-icon>
            </el-button>
          </el-tooltip>

          <div class="user-info">
            <el-avatar :size="32" :icon="User" />
            <span class="username">私人模式</span>
          </div>
        </div>
      </el-header>

      <!-- 内容区域 -->
      <el-main class="main-content">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
  <el-drawer v-model="mobileNavigationOpen" direction="ltr" size="280px" :with-header="false" class="mobile-navigation-drawer">
    <div class="mobile-drawer-logo">Aion Memory Nexus</div>
    <NavigationMenu :collapsed="false" @navigate="closeMobileNavigation" />
  </el-drawer>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useAppStore } from '../../stores/app'
import { Memo, Fold, Expand, Sunny, Moon, User } from '@element-plus/icons-vue'
import NavigationMenu from './NavigationMenu.vue'

const store = useAppStore()
const isMobile = ref(false)
const mobileNavigationOpen = ref(false)
const mediaQuery = typeof window === 'undefined' ? null : window.matchMedia('(max-width: 767px)')

const sidebarCollapsed = computed(() => isMobile.value || store.sidebarCollapsed)
const sidebarWidth = computed(() => sidebarCollapsed.value ? '64px' : '220px')
const navigationButtonLabel = computed(() => mobileNavigationOpen.value || !sidebarCollapsed.value ? '收起导航' : '展开导航')

const syncViewport = () => {
  isMobile.value = mediaQuery?.matches ?? false
  if (!isMobile.value) mobileNavigationOpen.value = false
}

const toggleNavigation = () => {
  if (isMobile.value) {
    mobileNavigationOpen.value = !mobileNavigationOpen.value
    return
  }
  store.toggleSidebar()
}

const closeMobileNavigation = () => {
  mobileNavigationOpen.value = false
}

onMounted(() => {
  syncViewport()
  mediaQuery?.addEventListener('change', syncViewport)
})

onBeforeUnmount(() => mediaQuery?.removeEventListener('change', syncViewport))
</script>

<style scoped>
.main-layout {
  height: 100vh;
  background: linear-gradient(135deg, #f5f7fa 0%, #e4e8ec 100%);
}

.sidebar {
  background: #ffffff;
  transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
  overflow: hidden;
  /* 增强阴影效果 - 多层阴影营造深度感 */
  box-shadow:
    0 0 20px rgba(0, 0, 0, 0.04),
    0 4px 12px rgba(0, 0, 0, 0.06),
    4px 0 8px rgba(0, 0, 0, 0.04);
  position: relative;
  z-index: 100;
}

/* 侧边栏顶部装饰线 */
.sidebar::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, #667eea 0%, #764ba2 50%, #667eea 100%);
  opacity: 0.9;
}

.logo {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #1a1a2e;
  font-size: 18px;
  font-weight: 700;
  border-bottom: 1px solid rgba(102, 126, 234, 0.1);
  background: linear-gradient(135deg, rgba(102, 126, 234, 0.03) 0%, rgba(118, 75, 162, 0.03) 100%);
  position: relative;
  overflow: hidden;
}

/* Logo 装饰效果 */
.logo::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent 0%, rgba(102, 126, 234, 0.3) 50%, transparent 100%);
}

.logo-text {
  white-space: nowrap;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.sidebar-menu {
  border-right: none;
  background: transparent !important;
}

.sidebar-menu:not(.el-menu--collapse) {
  width: 220px;
}

/* 菜单项悬停效果 */
:deep(.el-menu-item),
:deep(.el-sub-menu__title) {
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  border-radius: 8px;
  margin: 4px 8px;
  padding: 0 12px !important;
}

:deep(.el-menu-item:hover),
:deep(.el-sub-menu__title:hover) {
  background: linear-gradient(135deg, rgba(102, 126, 234, 0.08) 0%, rgba(118, 75, 162, 0.08) 100%) !important;
  transform: translateX(4px);
}

:deep(.el-menu-item.is-active) {
  background: linear-gradient(135deg, rgba(102, 126, 234, 0.12) 0%, rgba(118, 75, 162, 0.12) 100%) !important;
  position: relative;
}

:deep(.el-menu-item.is-active::before) {
  content: '';
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 20px;
  background: linear-gradient(180deg, #667eea 0%, #764ba2 100%);
  border-radius: 0 3px 3px 0;
}

.header {
  background: rgba(255, 255, 255, 0.95);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  /* 增强阴影 - 柔和的多层阴影 */
  box-shadow:
    0 1px 3px rgba(0, 0, 0, 0.02),
    0 4px 12px rgba(0, 0, 0, 0.04),
    0 12px 24px rgba(0, 0, 0, 0.02);
  position: relative;
  z-index: 50;
}

/* 顶部导航底部装饰 */
.header::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent 0%, rgba(102, 126, 234, 0.15) 50%, transparent 100%);
}

.header-left {
  display: flex;
  align-items: center;
}

.icon-button {
  font-size: 20px;
  color: #606266;
  min-width: 40px;
  min-height: 40px;
  transition: color 0.2s ease, background-color 0.2s ease;
}

.icon-button:hover {
  color: #667eea;
  background: rgba(102, 126, 234, 0.1);
}

.header-right {
  display: flex;
  align-items: center;
  gap: 20px;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  padding: 6px 12px;
  border-radius: 24px;
  transition: all 0.3s ease;
  background: linear-gradient(135deg, rgba(102, 126, 234, 0.05) 0%, rgba(118, 75, 162, 0.05) 100%);
}

.user-info:hover {
  background: linear-gradient(135deg, rgba(102, 126, 234, 0.12) 0%, rgba(118, 75, 162, 0.12) 100%);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(102, 126, 234, 0.15);
}

.username {
  color: #303133;
  font-size: 14px;
  font-weight: 500;
}

.main-content {
  padding: 24px;
  background: linear-gradient(135deg, #f5f7fa 0%, #e8ecf1 50%, #f0f4f8 100%);
  min-height: calc(100vh - 60px);
  position: relative;
}

/* 主内容区装饰背景 */
.main-content::before {
  content: '';
  position: fixed;
  top: 60px;
  right: 0;
  width: 400px;
  height: 400px;
  background: radial-gradient(circle, rgba(102, 126, 234, 0.03) 0%, transparent 70%);
  pointer-events: none;
}

.main-content::after {
  content: '';
  position: fixed;
  bottom: 0;
  left: 0;
  width: 300px;
  height: 300px;
  background: radial-gradient(circle, rgba(118, 75, 162, 0.03) 0%, transparent 70%);
  pointer-events: none;
}

.mobile-drawer-logo {
  height: 60px;
  display: flex;
  align-items: center;
  padding: 0 20px;
  color: #1a1a2e;
  font-size: 18px;
  font-weight: 700;
  border-bottom: 1px solid rgba(102, 126, 234, 0.1);
}

@media (max-width: 767px) {
  /* T08: 移动端侧栏完全隐藏，由 el-drawer 接管导航 */
  .sidebar-mobile {
    display: none;
  }

  .header {
    height: 56px;
    padding: 0 12px;
  }

  .header-right {
    gap: 8px;
  }

  .username {
    display: none;
  }

  .user-info {
    padding: 4px;
  }

  .main-content {
    padding: 16px;
    min-height: calc(100vh - 56px);
  }

  .main-content::before,
  .main-content::after {
    display: none;
  }
}
</style>

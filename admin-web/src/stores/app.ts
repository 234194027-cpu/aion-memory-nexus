import { defineStore } from 'pinia'
import { ref } from 'vue'

const THEME_KEY = 'app-theme'

export const useAppStore = defineStore('app', () => {
  const sidebarCollapsed = ref(false)
  // 从 localStorage 读取已保存的主题，默认 light
  const theme = ref<'light' | 'dark'>(
    (localStorage.getItem(THEME_KEY) as 'light' | 'dark') || 'light'
  )

  // Element Plus 暗色模式依赖 html 元素上的 .dark 类
  const applyTheme = () => {
    const html = document.documentElement
    if (theme.value === 'dark') {
      html.classList.add('dark')
    } else {
      html.classList.remove('dark')
    }
  }

  // 初始化时立即应用主题，避免首次渲染闪烁
  applyTheme()

  const toggleSidebar = () => {
    sidebarCollapsed.value = !sidebarCollapsed.value
  }

  const toggleTheme = () => {
    theme.value = theme.value === 'light' ? 'dark' : 'light'
    applyTheme()
    localStorage.setItem(THEME_KEY, theme.value)
  }

  return {
    sidebarCollapsed,
    theme,
    toggleSidebar,
    toggleTheme
  }
})

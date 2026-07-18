<template>
  <div class="lm-settings-section">
    <button
      type="button"
      class="lm-settings-section__header"
      :aria-expanded="!collapsed"
      :aria-controls="contentId"
      @click="toggle"
    >
      <el-icon class="lm-settings-section__chevron" :class="{ 'is-collapsed': collapsed }">
        <ArrowRight />
      </el-icon>
      <div class="lm-settings-section__title-group">
        <h3 class="lm-settings-section__title">{{ title }}</h3>
        <p v-if="description" class="lm-settings-section__description">{{ description }}</p>
      </div>
    </button>
    <div v-show="!collapsed" :id="contentId" class="lm-settings-section__content">
      <slot />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { ArrowRight } from '@element-plus/icons-vue'

defineOptions({ name: 'LmSettingsSection' })

const props = withDefaults(defineProps<{
  title: string
  description?: string
  defaultCollapsed?: boolean
}>(), {
  description: '',
  defaultCollapsed: false,
})

const collapsed = ref(props.defaultCollapsed)

watch(() => props.defaultCollapsed, (val) => {
  collapsed.value = val
})

const toggle = () => {
  collapsed.value = !collapsed.value
}

// 简单的唯一 id 用于 aria-controls 关联
const contentId = `lm-settings-section-${Math.random().toString(36).slice(2, 10)}`
</script>

<style scoped>
.lm-settings-section {
  border: 1px solid var(--lm-color-border);
  border-radius: var(--lm-radius-md);
  background: var(--lm-color-bg);
  margin-bottom: var(--lm-spacing-md);
  overflow: hidden;
}

.lm-settings-section__header {
  display: flex;
  align-items: center;
  gap: var(--lm-spacing-sm);
  width: 100%;
  padding: var(--lm-spacing-md) var(--lm-spacing-lg);
  background: transparent;
  border: none;
  cursor: pointer;
  text-align: left;
  font-family: inherit;
  color: var(--lm-color-text);
  transition: background var(--lm-transition);
}

.lm-settings-section__header:hover {
  background: var(--lm-color-bg-muted);
}

.lm-settings-section__header:focus-visible {
  outline: 2px solid var(--lm-color-primary);
  outline-offset: -2px;
}

.lm-settings-section__chevron {
  transition: transform var(--lm-transition);
  color: var(--lm-color-text-secondary);
  flex-shrink: 0;
}

.lm-settings-section__chevron.is-collapsed {
  transform: rotate(0deg);
}

.lm-settings-section__chevron:not(.is-collapsed) {
  transform: rotate(90deg);
}

.lm-settings-section__title-group {
  flex: 1;
  min-width: 0;
}

.lm-settings-section__title {
  margin: 0;
  font-size: var(--lm-font-size-lg);
  font-weight: 600;
  color: var(--lm-color-text);
  line-height: 1.3;
}

.lm-settings-section__description {
  margin: var(--lm-spacing-xs) 0 0 0;
  font-size: var(--lm-font-size-sm);
  color: var(--lm-color-text-secondary);
  line-height: 1.4;
}

.lm-settings-section__content {
  padding: var(--lm-spacing-md) var(--lm-spacing-lg);
  border-top: 1px solid var(--lm-color-border);
}
</style>

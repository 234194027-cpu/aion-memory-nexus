<template>
  <div class="lm-empty-state" role="status" aria-live="polite">
    <div class="lm-empty-state__icon" aria-hidden="true">
      <slot name="icon">
        <el-icon v-if="actionIcon" :size="48"><component :is="actionIcon" /></el-icon>
        <el-icon v-else :size="48"><DocumentRemove /></el-icon>
      </slot>
    </div>
    <p class="lm-empty-state__description">{{ description }}</p>
    <el-button
      v-if="actionText"
      type="primary"
      :icon="actionIcon"
      @click="handleAction"
    >
      {{ actionText }}
    </el-button>
  </div>
</template>

<script setup lang="ts">
import { DocumentRemove } from '@element-plus/icons-vue'
import type { Component } from 'vue'

defineOptions({ name: 'LmEmptyState' })

const props = defineProps<{
  description: string
  actionText?: string
  actionIcon?: Component
}>()

const emit = defineEmits<{ action: [] }>()

const handleAction = () => {
  // Guard: 仅在提供了 actionText 时才触发（按钮已由 v-if 控制）
  if (props.actionText) {
    emit('action')
  }
}
</script>

<style scoped>
.lm-empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: var(--lm-spacing-xl) var(--lm-spacing-lg);
  text-align: center;
  color: var(--lm-color-text-secondary);
}

.lm-empty-state__icon {
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: var(--lm-spacing-md);
  color: var(--lm-color-text-placeholder);
  opacity: 0.7;
}

.lm-empty-state__description {
  margin: 0 0 var(--lm-spacing-lg) 0;
  font-size: var(--lm-font-size-base);
  line-height: 1.6;
  max-width: 480px;
}
</style>

<template>
  <div class="lm-status-card" :class="`lm-status-card--${status}`">
    <div v-if="icon || $slots.icon" class="lm-status-card__icon">
      <slot name="icon">
        <el-icon v-if="icon" :size="24"><component :is="icon" /></el-icon>
      </slot>
    </div>
    <div class="lm-status-card__body">
      <div class="lm-status-card__title">{{ title }}</div>
      <div class="lm-status-card__value">{{ value }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { Component } from 'vue'

defineOptions({ name: 'LmStatusCard' })

withDefaults(defineProps<{
  title: string
  value: string | number
  status?: 'primary' | 'success' | 'warning' | 'danger' | 'info'
  icon?: Component
}>(), {
  status: 'primary',
})
</script>

<style scoped>
.lm-status-card {
  display: flex;
  align-items: center;
  gap: var(--lm-spacing-md);
  padding: var(--lm-spacing-md) var(--lm-spacing-lg);
  background: var(--lm-color-bg);
  border-radius: var(--lm-radius-lg);
  box-shadow: var(--lm-shadow-sm);
  border-left: 4px solid var(--lm-color-primary);
  transition: box-shadow var(--lm-transition), transform var(--lm-transition);
}

.lm-status-card:hover {
  box-shadow: var(--lm-shadow-md);
  transform: translateY(-2px);
}

.lm-status-card__icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  border-radius: var(--lm-radius-md);
  background: var(--lm-color-primary);
  color: #fff;
  flex-shrink: 0;
}

.lm-status-card__body {
  flex: 1;
  min-width: 0;
}

.lm-status-card__title {
  font-size: var(--lm-font-size-sm);
  color: var(--lm-color-text-secondary);
  margin-bottom: var(--lm-spacing-xs);
}

.lm-status-card__value {
  font-size: var(--lm-font-size-2xl);
  font-weight: 700;
  color: var(--lm-color-text);
  line-height: 1.2;
  word-break: break-word;
}

/* Status variants */
.lm-status-card--primary {
  border-left-color: var(--lm-color-primary);
}
.lm-status-card--primary .lm-status-card__icon {
  background: var(--lm-color-primary);
}

.lm-status-card--success {
  border-left-color: var(--lm-color-success);
}
.lm-status-card--success .lm-status-card__icon {
  background: var(--lm-color-success);
}

.lm-status-card--warning {
  border-left-color: var(--lm-color-warning);
}
.lm-status-card--warning .lm-status-card__icon {
  background: var(--lm-color-warning);
}

.lm-status-card--danger {
  border-left-color: var(--lm-color-danger);
}
.lm-status-card--danger .lm-status-card__icon {
  background: var(--lm-color-danger);
}

.lm-status-card--info {
  border-left-color: var(--lm-color-info);
}
.lm-status-card--info .lm-status-card__icon {
  background: var(--lm-color-info);
}
</style>

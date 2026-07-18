<template>
  <div class="lm-page-header">
    <div class="lm-page-header__main">
      <h1 class="lm-page-header__title">{{ title }}</h1>
      <p v-if="subtitle" class="lm-page-header__subtitle">{{ subtitle }}</p>
    </div>
    <div v-if="hasActions" class="lm-page-header__actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, useSlots } from 'vue'

defineOptions({ name: 'LmPageHeader' })

withDefaults(defineProps<{
  title: string
  subtitle?: string
}>(), {
  subtitle: '',
})

const slots = useSlots()
const hasActions = computed(() => Boolean(slots.actions))
</script>

<style scoped>
.lm-page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--lm-spacing-md);
  margin-bottom: var(--lm-spacing-lg);
  padding-bottom: var(--lm-spacing-md);
  border-bottom: 1px solid var(--lm-color-border);
}

.lm-page-header__main {
  flex: 1;
  min-width: 0;
}

.lm-page-header__title {
  margin: 0;
  font-size: var(--lm-font-size-2xl);
  font-weight: 700;
  color: var(--lm-color-text);
  line-height: 1.3;
  word-break: break-word;
}

.lm-page-header__subtitle {
  margin-top: var(--lm-spacing-xs);
  font-size: var(--lm-font-size-base);
  color: var(--lm-color-text-secondary);
  line-height: 1.5;
}

.lm-page-header__actions {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: var(--lm-spacing-sm);
}

@media (max-width: 767px) {
  .lm-page-header {
    flex-direction: column;
    align-items: stretch;
  }

  .lm-page-header__actions {
    justify-content: flex-end;
  }
}
</style>

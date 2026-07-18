<template>
  <el-drawer
    :model-value="modelValue"
    :title="title"
    direction="rtl"
    :size="drawerSize"
    :z-index="zIndex"
    @update:model-value="handleUpdate"
  >
    <div v-if="evidence.length === 0" class="lm-evidence-drawer__empty">
      <el-empty description="暂无证据追溯信息" :image-size="80" />
    </div>
    <ul v-else class="lm-evidence-drawer__list">
      <li
        v-for="(item, index) in evidence"
        :key="item.memory_id || index"
        class="lm-evidence-drawer__item"
      >
        <div class="lm-evidence-drawer__row">
          <span class="lm-evidence-drawer__label">记忆 ID</span>
          <span class="lm-evidence-drawer__value">{{ item.memory_id || '—' }}</span>
        </div>
        <div v-if="item.source_ref" class="lm-evidence-drawer__row">
          <span class="lm-evidence-drawer__label">来源</span>
          <span class="lm-evidence-drawer__value">{{ item.source_ref }}</span>
        </div>
        <div v-if="item.valid_time" class="lm-evidence-drawer__row">
          <span class="lm-evidence-drawer__label">有效时间</span>
          <span class="lm-evidence-drawer__value">{{ formatTime(item.valid_time) }}</span>
        </div>
        <div v-if="item.epistemic_status" class="lm-evidence-drawer__row">
          <span class="lm-evidence-drawer__label">认识论状态</span>
          <el-tag size="small" :type="epistemicTagType(item.epistemic_status)">
            {{ item.epistemic_status }}
          </el-tag>
        </div>
      </li>
    </ul>
  </el-drawer>
</template>

<script setup lang="ts">
defineOptions({ name: 'LmEvidenceDrawer' })

withDefaults(defineProps<{
  modelValue: boolean
  evidence: Array<{
    memory_id?: string
    source_ref?: string
    valid_time?: string | null
    epistemic_status?: string
  }>
  title?: string
  drawerSize?: string | number
  zIndex?: number
}>(), {
  title: '证据追溯',
  drawerSize: '420px',
  zIndex: 2000,
})

const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

const handleUpdate = (val: boolean) => emit('update:modelValue', val)

const formatTime = (raw: string | null | undefined): string => {
  if (!raw) return '—'
  // 已是 ISO 字符串或可读字符串时直接返回；不强制解析避免误判
  return raw
}

const epistemicTagType = (status: string): 'success' | 'warning' | 'info' | 'primary' => {
  const lowered = status.toLowerCase()
  if (lowered.includes('confirmed') || lowered.includes('verified')) return 'success'
  if (lowered.includes('tentative') || lowered.includes('hypothetical')) return 'warning'
  if (lowered.includes('deprecated') || lowered.includes('superseded')) return 'info'
  return 'primary'
}
</script>

<style scoped>
.lm-evidence-drawer__empty {
  padding: var(--lm-spacing-xl) 0;
}

.lm-evidence-drawer__list {
  list-style: none;
  padding: 0;
  margin: 0;
}

.lm-evidence-drawer__item {
  padding: var(--lm-spacing-md);
  border: 1px solid var(--lm-color-border);
  border-radius: var(--lm-radius-md);
  margin-bottom: var(--lm-spacing-sm);
  background: var(--lm-color-bg);
  transition: box-shadow var(--lm-transition);
}

.lm-evidence-drawer__item:hover {
  box-shadow: var(--lm-shadow-sm);
}

.lm-evidence-drawer__row {
  display: flex;
  align-items: flex-start;
  gap: var(--lm-spacing-sm);
  margin-bottom: var(--lm-spacing-xs);
  font-size: var(--lm-font-size-sm);
  line-height: 1.5;
}

.lm-evidence-drawer__row:last-child {
  margin-bottom: 0;
}

.lm-evidence-drawer__label {
  flex-shrink: 0;
  width: 84px;
  color: var(--lm-color-text-secondary);
}

.lm-evidence-drawer__value {
  flex: 1;
  word-break: break-all;
  color: var(--lm-color-text);
}

@media (max-width: 767px) {
  .lm-evidence-drawer__row {
    flex-direction: column;
    gap: 2px;
  }

  .lm-evidence-drawer__label {
    width: auto;
  }
}
</style>

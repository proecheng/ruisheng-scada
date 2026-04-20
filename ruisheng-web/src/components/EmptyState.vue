<script setup lang="ts">
withDefaults(
  defineProps<{
    icon?: string
    title?: string
    description?: string
    actionLabel?: string
  }>(),
  { icon: '📭', title: '暂无数据' },
)
defineEmits<{ action: [] }>()
</script>

<template>
  <div class="empty">
    <div class="icon">{{ icon }}</div>
    <div class="title">{{ title }}</div>
    <div v-if="description" class="desc">{{ description }}</div>
    <button v-if="actionLabel" class="action" @click="$emit('action')">
      {{ actionLabel }}
    </button>
  </div>
</template>

<style scoped>
.empty {
  padding: 48px 16px;
  text-align: center;
  color: var(--color-text-secondary);
}
.icon { font-size: 48px; margin-bottom: 12px; }
.title { font-size: 16px; color: var(--color-text); margin-bottom: 8px; }
.desc { font-size: 13px; margin-bottom: 16px; }
.action {
  background: var(--color-primary);
  color: white;
  border: none;
  padding: 8px 16px;
  border-radius: 4px;
  cursor: pointer;
}
</style>

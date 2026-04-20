<script setup lang="ts">
withDefaults(defineProps<{ lines?: number; height?: string }>(), {
  lines: 3,
  height: '14px',
})
</script>

<template>
  <div class="skeleton" aria-busy="true" aria-label="加载中">
    <div
      v-for="n in lines"
      :key="n"
      class="line"
      :style="{ height, width: `${70 + Math.floor(Math.random() * 30)}%` }"
    />
  </div>
</template>

<style scoped>
.skeleton { display: flex; flex-direction: column; gap: 8px; padding: 8px 0; }
.line {
  background: linear-gradient(90deg, #eee 0%, #f5f5f5 50%, #eee 100%);
  background-size: 200% 100%;
  animation: shimmer 1.2s linear infinite;
  border-radius: 4px;
}
@keyframes shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
</style>

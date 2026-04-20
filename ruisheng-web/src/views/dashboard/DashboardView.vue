<script setup lang="ts">
import { useAuthStore } from '@/stores/auth'
const auth = useAuthStore()
</script>

<template>
  <section>
    <h2>概览</h2>
    <p>欢迎，{{ auth.user?.user_name }}（{{ auth.user?.authority }}）</p>
    <p class="todo">TODO: 设备总览卡片 + 告警摘要 + 实时图表（Task D3）</p>
  </section>
</template>

<style scoped>
h2 { font-size: 18px; margin-bottom: 12px; }
.todo { color: var(--color-text-secondary); font-size: 13px; }
</style>

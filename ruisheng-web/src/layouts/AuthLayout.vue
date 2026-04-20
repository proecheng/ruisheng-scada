<script setup lang="ts">
import { RouterView } from 'vue-router'
</script>

<template>
  <div class="auth-shell">
    <div class="auth-card">
      <h1 class="auth-title">江苏润盛 SCADA</h1>
      <RouterView />
    </div>
  </div>
</template>

<style scoped>
.auth-shell {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-bg-alt);
}
.auth-card {
  width: min(420px, 92vw);
  background: var(--color-bg);
  padding: 32px;
  border-radius: 8px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
}
.auth-title {
  text-align: center;
  font-size: 20px;
  margin-bottom: 24px;
  color: var(--color-primary);
}
</style>

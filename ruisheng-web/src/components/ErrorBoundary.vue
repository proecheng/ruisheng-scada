<script setup lang="ts">
import { onErrorCaptured, ref } from 'vue'
import { useDiagStore } from '@/stores/diag'

const hasError = ref(false)
const errMsg = ref('')
const diag = useDiagStore()

onErrorCaptured((err) => {
  hasError.value = true
  errMsg.value = err instanceof Error ? err.message : String(err)
  diag.record({
    at: new Date().toISOString(),
    kind: 'error',
    label: errMsg.value,
    detail: err instanceof Error ? err.stack : undefined,
  })
  return false
})

function retry(): void {
  hasError.value = false
  errMsg.value = ''
}
function reload(): void {
  window.location.reload()
}
</script>

<template>
  <div v-if="hasError" class="boundary">
    <div class="card">
      <h3>该模块异常</h3>
      <p class="msg">{{ errMsg }}</p>
      <div class="actions">
        <button @click="retry">重试</button>
        <button @click="reload">刷新页面</button>
      </div>
    </div>
  </div>
  <slot v-else />
</template>

<style scoped>
.boundary {
  padding: 32px;
  display: flex;
  justify-content: center;
}
.card {
  background: #fff;
  border: 1px solid var(--color-error);
  border-radius: 8px;
  padding: 24px;
  max-width: 480px;
}
.msg { color: var(--color-error); margin: 12px 0; font-family: monospace; font-size: 13px; }
.actions { display: flex; gap: 8px; }
button { padding: 6px 14px; border: 1px solid #ccc; border-radius: 4px; cursor: pointer; }
</style>

<script setup lang="ts">
import { useToastStore, useToast } from '@/composables/useToast'

const store = useToastStore()
const { dismiss } = useToast()

async function copy(txt: string): Promise<void> {
  try { await navigator.clipboard?.writeText(txt) } catch { /* ignore */ }
}
</script>

<template>
  <div class="toast-host" role="status" aria-live="polite">
    <div
      v-for="t in store.toasts"
      :key="t.id"
      class="toast"
      :class="[`t-${t.type}`]"
    >
      <div class="body">
        <div class="msg">{{ t.message }}</div>
        <div v-if="t.hint" class="hint">{{ t.hint }}</div>
        <div v-if="t.traceId" class="trace" @click="copy(t.traceId!)">
          Trace ID: <code>{{ t.traceId }}</code> (点击复制)
        </div>
      </div>
      <button class="close" aria-label="close" @click="dismiss(t.id)">×</button>
    </div>
  </div>
</template>

<style scoped>
.toast-host {
  position: fixed;
  top: 16px;
  right: 16px;
  z-index: 1000;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: 380px;
}
.toast {
  display: flex;
  gap: 12px;
  background: #fff;
  border-left: 4px solid var(--color-primary);
  box-shadow: 0 2px 8px rgba(0,0,0,0.15);
  padding: 10px 12px;
  border-radius: 4px;
}
.t-success { border-color: var(--color-success); }
.t-warning { border-color: var(--color-warning); }
.t-error { border-color: var(--color-error); }
.body { flex: 1; font-size: 13px; }
.msg { font-weight: 500; }
.hint { color: var(--color-text-secondary); margin-top: 2px; }
.trace { color: var(--color-text-secondary); font-size: 11px; margin-top: 4px; cursor: pointer; }
.close {
  background: none;
  border: none;
  font-size: 18px;
  cursor: pointer;
  color: var(--color-text-secondary);
}
</style>

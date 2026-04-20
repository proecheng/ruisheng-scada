<script setup lang="ts">
import { useDiagStore } from '@/stores/diag'
const diag = useDiagStore()
const apiEntries = () => diag.entries.filter((e) => e.kind === 'api' || e.kind === 'error')
</script>

<template>
  <div class="panel">
    <header><strong>API 日志</strong><button @click="diag.clear()">清空</button></header>
    <ul>
      <li v-for="(e, i) in apiEntries()" :key="i" :class="e.kind">
        <span class="at">{{ e.at.slice(11, 23) }}</span>
        <span class="label">{{ e.label }}</span>
        <span v-if="e.durationMs !== undefined" class="dur">{{ e.durationMs }}ms</span>
        <span v-if="e.traceId" class="tid">{{ e.traceId.slice(0,8) }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.panel { background: #fafafa; border: 1px solid #ddd; border-radius: 4px; font-size: 11px; padding: 8px; max-height: 180px; overflow-y: auto; }
header { display: flex; justify-content: space-between; margin-bottom: 6px; }
header button { font-size: 10px; }
ul { list-style: none; }
li { display: flex; gap: 8px; padding: 2px 0; border-bottom: 1px dotted #eee; font-family: monospace; }
li.error { color: var(--color-error); }
.at { color: var(--color-text-secondary); width: 80px; }
.label { flex: 1; }
.dur { color: var(--color-primary); }
.tid { color: var(--color-text-secondary); }
</style>

<script setup lang="ts">
import { useWsStore } from '@/stores/ws'
const ws = useWsStore()
</script>

<template>
  <div class="panel">
    <header><strong>WebSocket 状态</strong></header>
    <dl>
      <dt>状态</dt>
      <dd :data-state="ws.state">{{ ws.state }}</dd>
      <dt>累计消息</dt>
      <dd>{{ ws.messageCount }}</dd>
      <dt>最新消息</dt>
      <dd><code>{{ ws.lastMessage ? JSON.stringify(ws.lastMessage).slice(0, 80) : '—' }}</code></dd>
    </dl>
  </div>
</template>

<style scoped>
.panel { background: #fafafa; border: 1px solid #ddd; border-radius: 4px; font-size: 11px; padding: 8px; }
header { margin-bottom: 6px; }
dl { display: grid; grid-template-columns: 90px 1fr; gap: 4px; }
dt { color: var(--color-text-secondary); }
dd[data-state='open'] { color: var(--color-success); }
dd[data-state='reconnecting'], dd[data-state='connecting'] { color: var(--color-warning); }
dd[data-state='closed'] { color: var(--color-error); }
code { font-family: monospace; word-break: break-all; }
</style>

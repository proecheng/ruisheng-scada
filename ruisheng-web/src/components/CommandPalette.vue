<script setup lang="ts">
import { ref, computed, nextTick, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useShortcuts } from '@/composables/useShortcuts'
import { useDevicesStore } from '@/stores/devices'

const open = ref(false)
const query = ref('')
const inputEl = ref<HTMLInputElement | null>(null)
const router = useRouter()
const devices = useDevicesStore()

interface PaletteItem {
  label: string
  hint?: string
  action: () => void
}

const staticItems: PaletteItem[] = [
  { label: '概览', action: () => router.push('/dashboard') },
  { label: '设备列表', action: () => router.push('/devices') },
  { label: '告警列表', action: () => router.push('/alarms') },
  { label: '日报表', action: () => router.push('/reports') },
  { label: '波形分析', action: () => router.push('/waveforms') },
  { label: '组态', action: () => router.push('/scenes') },
  { label: '用户管理', action: () => router.push('/settings/users') },
]

const items = computed<PaletteItem[]>(() => {
  const q = query.value.trim().toLowerCase()
  const deviceItems: PaletteItem[] = devices.list.map((d) => ({
    label: `${d.dev_number} — ${d.dev_name}`,
    hint: d.state,
    action: () => router.push(`/devices/${d.dev_number}`),
  }))
  const all = [...staticItems, ...deviceItems]
  if (!q) return all.slice(0, 20)
  return all.filter((i) => i.label.toLowerCase().includes(q)).slice(0, 20)
})

const selectedIndex = ref(0)

watch(query, () => (selectedIndex.value = 0))

useShortcuts([
  {
    key: 'k',
    ctrl: true,
    handler: () => {
      open.value = true
      void nextTick(() => inputEl.value?.focus())
    },
  },
  {
    key: 'k',
    meta: true,
    handler: () => {
      open.value = true
      void nextTick(() => inputEl.value?.focus())
    },
  },
  { key: 'Escape', handler: () => (open.value = false), preventDefault: false },
])

function execute(item: PaletteItem): void {
  item.action()
  open.value = false
  query.value = ''
}

function onKeyDown(e: KeyboardEvent): void {
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    selectedIndex.value = Math.min(selectedIndex.value + 1, items.value.length - 1)
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    selectedIndex.value = Math.max(selectedIndex.value - 1, 0)
  } else if (e.key === 'Enter') {
    e.preventDefault()
    const it = items.value[selectedIndex.value]
    if (it) execute(it)
  }
}
</script>

<template>
  <div v-if="open" class="cp-backdrop" @click.self="open = false">
    <div class="cp">
      <input
        ref="inputEl"
        v-model="query"
        class="cp-input"
        placeholder="搜索设备 / 功能…"
        @keydown="onKeyDown"
      />
      <ul class="cp-list">
        <li
          v-for="(item, i) in items"
          :key="item.label"
          :class="{ selected: i === selectedIndex }"
          @click="execute(item)"
        >
          <span>{{ item.label }}</span>
          <span v-if="item.hint" class="hint">{{ item.hint }}</span>
        </li>
        <li v-if="items.length === 0" class="empty">未找到匹配项</li>
      </ul>
      <div class="cp-footer">Ctrl+K 打开 · ↑↓ 选择 · Enter 执行 · Esc 关闭</div>
    </div>
  </div>
</template>

<style scoped>
.cp-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.3);
  display: flex;
  justify-content: center;
  padding-top: 10vh;
  z-index: 800;
}
.cp {
  background: #fff;
  border-radius: 8px;
  width: min(560px, 92vw);
  box-shadow: 0 8px 32px rgba(0,0,0,0.25);
  overflow: hidden;
}
.cp-input {
  width: 100%;
  border: none;
  padding: 14px 16px;
  font-size: 14px;
  border-bottom: 1px solid #eee;
  outline: none;
}
.cp-list {
  list-style: none;
  max-height: 340px;
  overflow-y: auto;
}
.cp-list li {
  padding: 8px 16px;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  font-size: 14px;
}
.cp-list li.selected { background: #eef5fd; }
.cp-list li.empty { color: var(--color-text-secondary); cursor: default; }
.hint { color: var(--color-text-secondary); font-size: 12px; }
.cp-footer {
  padding: 6px 16px;
  background: #f7f7f7;
  font-size: 11px;
  color: var(--color-text-secondary);
  border-top: 1px solid #eee;
}
</style>

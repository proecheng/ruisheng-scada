<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { listAlarms, resetAlarm, type AlarmRecord } from '@/api/alarms'
import { useAsync } from '@/composables/useAsync'
import { useToast } from '@/composables/useToast'
import { useAlarmsStore } from '@/stores/alarms'
import { useWsStore } from '@/stores/ws'
import LoadingSkeleton from '@/components/LoadingSkeleton.vue'
import EmptyState from '@/components/EmptyState.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'

const router = useRouter()
const toast = useToast()
const alarmsStore = useAlarmsStore()
const wsStore = useWsStore()

const items = ref<AlarmRecord[]>([])
const cursor = ref<string | null>(null)
const filters = ref({ severity: '', acked: '' as '' | 'true' | 'false' })
const selected = ref<Set<number>>(new Set())
const showResetDialog = ref(false)

const loader = useAsync(() =>
  listAlarms({
    severity: filters.value.severity || undefined,
    acked: filters.value.acked === '' ? undefined : filters.value.acked === 'true',
    cursor: cursor.value ?? undefined,
  }),
)

async function load(reset = false): Promise<void> {
  if (reset) {
    cursor.value = null
    items.value = []
  }
  const page = await loader.run()
  if (reset) items.value = page.items
  else items.value.push(...page.items)
  cursor.value = page.next_cursor
}

onMounted(() => load(true))

watch(filters, () => load(true), { deep: true })

watch(
  () => alarmsStore.feed,
  (feed) => {
    for (const a of feed.slice(0, 10)) {
      if (!items.value.some((x) => x.event_id === a.event_id)) {
        items.value.unshift({
          event_id: a.event_id,
          dev_number: a.dev_number,
          cfg_id: 0,
          alarm_name: a.alarm_name,
          value: a.value,
          limit: a.limit,
          severity: 'warning',
          ts: a.ts,
        })
      }
    }
  },
  { deep: true },
)

function toggleSelect(id: number): void {
  if (selected.value.has(id)) selected.value.delete(id)
  else selected.value.add(id)
  selected.value = new Set(selected.value)
}

function selectAll(): void {
  const unacked = items.value.filter((i) => !i.acked_at)
  if (selected.value.size === unacked.length) selected.value = new Set()
  else selected.value = new Set(unacked.map((i) => i.event_id))
}

async function doReset(): Promise<void> {
  const ids = [...selected.value]
  let ok = 0
  for (const id of ids) {
    try {
      await resetAlarm(id)
      ok++
    } catch {
      /* ignore per-item */
    }
  }
  toast.success(`${ok}/${ids.length} 条已复位`)
  selected.value = new Set()
  await load(true)
}
</script>

<template>
  <section class="alarm-list">
    <header>
      <h2>告警列表 <small class="live" :data-on="wsStore.isHealthy">{{ wsStore.isHealthy ? '实时' : '离线' }}</small></h2>
      <div class="filters">
        <select v-model="filters.severity">
          <option value="">所有严重度</option>
          <option value="info">info</option>
          <option value="warning">warning</option>
          <option value="critical">critical</option>
        </select>
        <select v-model="filters.acked">
          <option value="">全部</option>
          <option value="false">未复位</option>
          <option value="true">已复位</option>
        </select>
        <button class="action" :disabled="selected.size === 0" @click="showResetDialog = true">
          批量复位（{{ selected.size }}）
        </button>
      </div>
    </header>

    <LoadingSkeleton v-if="loader.isPending.value && items.length === 0" :lines="5" />
    <EmptyState v-else-if="items.length === 0" title="暂无告警" />
    <table v-else class="alarm-table">
      <thead>
        <tr>
          <th><input type="checkbox" @click="selectAll" /></th>
          <th>时间</th><th>设备</th><th>规则</th><th>值/阈值</th><th>严重度</th><th>状态</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="a in items"
          :key="a.event_id"
          :class="{ acked: !!a.acked_at }"
          @click="router.push(`/devices/${a.dev_number}`)"
        >
          <td @click.stop>
            <input
              type="checkbox"
              :disabled="!!a.acked_at"
              :checked="selected.has(a.event_id)"
              @change="toggleSelect(a.event_id)"
            />
          </td>
          <td>{{ new Date(a.ts).toLocaleString() }}</td>
          <td><code>{{ a.dev_number }}</code></td>
          <td>{{ a.alarm_name }}</td>
          <td>{{ a.value }} / {{ a.limit }}</td>
          <td><span class="pill" :data-sev="a.severity">{{ a.severity }}</span></td>
          <td>{{ a.acked_at ? `✓ ${a.acked_by ?? ''}` : '未复位' }}</td>
        </tr>
      </tbody>
    </table>

    <button v-if="cursor" class="more" :disabled="loader.isPending.value" @click="load(false)">
      {{ loader.isPending.value ? '加载中…' : '加载更多' }}
    </button>

    <ConfirmDialog
      v-model="showResetDialog"
      :message="`确认复位 ${selected.size} 条告警？`"
      :type-to-confirm="selected.size >= 5 ? 'CONFIRM' : undefined"
      @confirm="doReset"
    />
  </section>
</template>

<style scoped>
.alarm-list { background: #fff; padding: 16px; border-radius: 6px; }
header { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-bottom: 16px; }
h2 { flex: 1; font-size: 18px; }
.live { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: #eee; color: var(--color-text-secondary); margin-left: 6px; }
.live[data-on='true'] { background: #e8f5e9; color: var(--color-success); }
.filters { display: flex; gap: 8px; }
.filters select { padding: 4px 8px; border: 1px solid #ccc; border-radius: 4px; font-size: 13px; }
.action { background: var(--color-primary); color: white; border: none; padding: 4px 12px; border-radius: 4px; cursor: pointer; }
.action:disabled { opacity: 0.5; cursor: not-allowed; }
.alarm-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.alarm-table th, .alarm-table td { padding: 8px 10px; border-bottom: 1px solid #eee; text-align: left; }
.alarm-table tbody tr { cursor: pointer; }
.alarm-table tbody tr:hover { background: #f7f7f7; }
.alarm-table tr.acked { color: var(--color-text-secondary); }
.pill { padding: 2px 8px; border-radius: 10px; font-size: 11px; }
.pill[data-sev='info'] { background: #e3f2fd; color: var(--color-primary); }
.pill[data-sev='warning'] { background: #fff3e0; color: var(--color-warning); }
.pill[data-sev='critical'] { background: #ffebee; color: var(--color-error); }
.more { display: block; margin: 16px auto; padding: 8px 24px; background: #fff; border: 1px solid #ccc; border-radius: 4px; cursor: pointer; }
</style>

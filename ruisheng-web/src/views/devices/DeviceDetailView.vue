<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { getDevice, getRealtime, setDeviceEnabled, type Device, type RealtimePoint } from '@/api/devices'
import { useWsStore } from '@/stores/ws'
import { useRecent } from '@/composables/useRecent'
import { useAsync } from '@/composables/useAsync'
import { useToast } from '@/composables/useToast'
import LoadingSkeleton from '@/components/LoadingSkeleton.vue'

const props = defineProps<{ devNumber: string }>()
const router = useRouter()
const toast = useToast()
const wsStore = useWsStore()
const recent = useRecent<string>('devices', 5)

const device = ref<Device | null>(null)
const points = ref<RealtimePoint[]>([])
const snapshotLoader = useAsync(() => getRealtime(props.devNumber))
const deviceLoader = useAsync(() => getDevice(props.devNumber))

let pollTimer: ReturnType<typeof setInterval> | null = null

async function loadAll(): Promise<void> {
  const [dev, snap] = await Promise.all([deviceLoader.run(), snapshotLoader.run()])
  device.value = dev
  points.value = snap.points
}

onMounted(async () => {
  recent.push(props.devNumber)
  await loadAll()
  pollTimer = setInterval(() => {
    if (!wsStore.isHealthy) void snapshotLoader.run().then((snap) => (points.value = snap.points))
  }, 10000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})

watch(
  () => wsStore.lastMessage,
  (m) => {
    if (!m || m.type !== 'realtime') return
    if (m.dev_number !== props.devNumber) return
    const p = points.value.find((x) => x.point_id === m.point_id)
    if (p) {
      p.value = m.value
      p.ts = m.ts
    }
  },
)

function openHistory(p: RealtimePoint): void {
  router.push({
    path: `/devices/${props.devNumber}/history`,
    query: { point_id: p.point_id },
  })
}

async function toggleEnabled(): Promise<void> {
  if (!device.value) return
  try {
    device.value = await setDeviceEnabled(props.devNumber, !(device.value.is_enabled ?? true))
    toast.success(device.value.is_enabled ? '设备已启用' : '设备已停用')
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '切换失败')
  }
}

const ageLabel = computed(
  () => (ts: string) => {
    const diff = (Date.now() - new Date(ts).getTime()) / 1000
    if (diff < 60) return `${Math.floor(diff)} 秒前`
    if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
    return `${Math.floor(diff / 3600)} 小时前`
  },
)
</script>

<template>
  <section class="device-detail">
    <header>
      <button class="back" @click="router.back()">← 返回</button>
      <h2 v-if="device">{{ device.dev_number }} — {{ device.dev_name }}</h2>
      <LoadingSkeleton v-else :lines="1" />
      <div v-if="device" class="summary">
        <span :data-enabled="device.is_enabled !== false">{{ device.is_enabled === false ? '已停用' : '已启用' }}</span>
        <span>{{ device.transport_type === 'serial' ? `串口 ${device.serial_port}` : `TCP ${device.dev_ip || '不限来源 IP'}` }}</span>
        <span>Modbus {{ device.modbus_addr ?? '—' }}</span>
      </div>
      <nav class="tabs">
        <button @click="router.push(`/devices/${devNumber}`)">实时</button>
        <button @click="router.push(`/devices/${devNumber}/history`)">历史</button>
        <button @click="router.push(`/devices/${devNumber}/control`)">控制</button>
        <button @click="router.push(`/devices/${devNumber}/points`)">点位配置</button>
        <button v-permission="['Administrators','GroupCompany','Company']" @click="router.push(`/devices/${devNumber}/edit`)">编辑</button>
        <button v-permission="['Administrators','GroupCompany','Company']" @click="toggleEnabled">
          {{ device?.is_enabled === false ? '启用' : '停用' }}
        </button>
      </nav>
    </header>

    <LoadingSkeleton v-if="snapshotLoader.isPending.value" :lines="5" />

    <div v-else class="points-grid">
      <div
        v-for="p in points"
        :key="p.point_id"
        class="point-card"
        @click="openHistory(p)"
      >
        <div class="p-name">{{ p.point_name ?? `点位 ${p.point_id}` }}</div>
        <div class="p-value">
          {{ p.value }}<span class="unit">{{ p.unit ?? '' }}</span>
        </div>
        <div class="p-ts">{{ ageLabel(p.ts) }}</div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.device-detail { background: #fff; padding: 16px; border-radius: 6px; }
header { margin-bottom: 16px; }
.back { background: none; border: 1px solid #ccc; padding: 4px 10px; border-radius: 4px; cursor: pointer; margin-right: 8px; }
h2 { display: inline; font-size: 18px; }
.summary { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; font-size: 12px; color: var(--color-text-secondary); }
.summary span { border: 1px solid #e5e7eb; border-radius: 4px; padding: 3px 7px; }
.summary span[data-enabled='true'] { color: var(--color-success); border-color: rgba(82, 196, 26, 0.35); }
.summary span[data-enabled='false'] { color: var(--color-error); border-color: rgba(245, 34, 45, 0.35); }
.tabs { margin-top: 12px; display: flex; gap: 4px; border-bottom: 1px solid #eee; }
.tabs button {
  background: none; border: none; padding: 8px 14px; cursor: pointer;
  border-bottom: 2px solid transparent; font-size: 14px;
}
.tabs button:hover { color: var(--color-primary); border-color: var(--color-primary); }
.points-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 10px; }
.point-card { padding: 12px; border: 1px solid #e0e0e0; border-radius: 6px; cursor: pointer; transition: border-color 0.15s; }
.point-card:hover { border-color: var(--color-primary); }
.p-name { font-size: 13px; color: var(--color-text-secondary); }
.p-value { font-size: 22px; font-weight: 600; margin: 6px 0; }
.p-value .unit { font-size: 13px; color: var(--color-text-secondary); margin-left: 4px; }
.p-ts { font-size: 11px; color: var(--color-text-secondary); }
</style>

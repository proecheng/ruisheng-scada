<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { getDevice, getRealtime, setDeviceEnabled, type Device, type RealtimePoint } from '@/api/devices'
import { useWsStore } from '@/stores/ws'
import { useRecent } from '@/composables/useRecent'
import { useAsync } from '@/composables/useAsync'
import { useToast } from '@/composables/useToast'
import LoadingSkeleton from '@/components/LoadingSkeleton.vue'
import { digitalChannels, formatPointValue } from '@/utils/pointDisplay'
import { listPoints, type PointConfig } from '@/api/points'
import { twoChannelPoint } from '@/utils/digitalPointBinding'
import { useAuthStore } from '@/stores/auth'
import DoControlPanel from '@/components/DoControlPanel.vue'

const props = defineProps<{ devNumber: string }>()
const router = useRouter()
const toast = useToast()
const wsStore = useWsStore()
const recent = useRecent<string>('devices', 5)
const auth = useAuthStore()
const pointConfigs = ref<PointConfig[]>([])
const inputPoint = computed(() => twoChannelPoint(pointConfigs.value, device.value, 0))
const outputPoint = computed(() => twoChannelPoint(pointConfigs.value, device.value, 1))
const showDigitalPanel = computed(() => !!inputPoint.value && !!outputPoint.value)
const inputSample = computed(() => points.value.find(p => p.point_id === inputPoint.value?.point_id) ?? null)
const outputSample = computed(() => points.value.find(p => p.point_id === outputPoint.value?.point_id) ?? null)
const canControl = computed(() => !!auth.user && auth.user.authority !== 'User' && !!((auth.user.control_authority ?? 0) & 1))
const hiddenPointIds = computed(() => new Set(pointConfigs.value.filter((p) => p.show === false).map((p) => p.point_id)))
const remainingPoints = computed(() => showDigitalPanel.value
  ? points.value.filter(p => !hiddenPointIds.value.has(p.point_id) && p.point_id !== inputPoint.value?.point_id && p.point_id !== outputPoint.value?.point_id)
  : points.value.filter(p => !hiddenPointIds.value.has(p.point_id)))

const device = ref<Device | null>(null)
const points = ref<RealtimePoint[]>([])
const snapshotLoader = useAsync(() => getRealtime(props.devNumber))
const deviceLoader = useAsync(() => getDevice(props.devNumber))

const now = ref(Date.now())
const refreshError = ref('')
const deviceError = ref('')
const intervalMs = computed(() => Math.max(1000, (device.value?.update_interval_decisec ?? 50) * 100))
let reloadSnapshot: () => Promise<void> = async () => {}
let ageTimer: ReturnType<typeof setInterval> | null = null

function validSample(p: { value: number | null; ts: string }): boolean {
  return typeof p.ts === 'string' && Number.isFinite(Date.parse(p.ts)) &&
    (p.value === null || (typeof p.value === 'number' && Number.isFinite(p.value)))
}

function mergeSnapshot(incoming: RealtimePoint[]): void {
  points.value = incoming.map((p) => {
    const current = points.value.find((item) => item.point_id === p.point_id)
    if (current && validSample(current) && (!validSample(p) || Date.parse(current.ts) > Date.parse(p.ts))) {
      return { ...p, value: current.value, ts: current.ts }
    }
    return p
  })
}

watch(() => props.devNumber, (devNumber, _old, onCleanup) => {
  let active = true
  let pending = false
  let timer: ReturnType<typeof setTimeout> | null = null
  device.value = null
  pointConfigs.value = []
  points.value = []
  refreshError.value = ''
  deviceError.value = ''
  recent.push(devNumber)
  const refresh = async (initial = false): Promise<void> => {
    if (!active || pending) return
    pending = true
    try {
      const snap = initial ? await snapshotLoader.run() : await getRealtime(devNumber)
      if (active) {
        mergeSnapshot(snap.points)
        refreshError.value = ''
      }
    } catch {
      if (active) refreshError.value = '刷新失败，保留上次数据，正在重试'
    } finally { pending = false }
  }
  reloadSnapshot = () => refresh()
  const tick = async () => {
    // A socket may remain open even when messages are lost. Reconcile the snapshot
    // at the device's configured cadence without hiding cards or overlapping requests.
    await refresh()
    if (active) timer = setTimeout(tick, intervalMs.value)
  }
  void deviceLoader.run().then(async (dev) => {
    if (!active) return
    device.value = dev
    if (dev.transport_type === 'serial' && dev.read_profile === 'zero_origin_38') {
      try { const config = await listPoints(devNumber); if (active) pointConfigs.value = config }
      catch { if (active) deviceError.value = '输入输出通道配置加载失败，保留原始点位显示' }
    }
  }).catch(() => {
    if (active) deviceError.value = '设备信息加载失败，请返回重试'
  })
  void refresh(true).finally(() => {
    if (active) timer = setTimeout(tick, intervalMs.value)
  })
  onCleanup(() => { active = false; if (timer) clearTimeout(timer) })
}, { immediate: true })

onMounted(() => { ageTimer = setInterval(() => { now.value = Date.now() }, 1000) })
onUnmounted(() => { if (ageTimer) clearInterval(ageTimer) })

watch(
  () => wsStore.lastMessage,
  (m) => {
    if (!m || m.type !== 'realtime' || m.dev_number !== props.devNumber || !validSample(m)) return
    const p = points.value.find((x) => x.point_id === m.point_id)
    if (p && (!validSample(p) || Date.parse(m.ts) >= Date.parse(p.ts))) {
      p.value = m.value
      p.ts = m.ts
    }
  },
  { flush: 'sync' },
)

function openHistory(p: RealtimePoint): void {
  router.push({
    path: `/devices/${props.devNumber}/history`,
    query: { point_id: p.point_id },
  })
}

async function reloadDigitalConfig(): Promise<void> {
  const devNumber = props.devNumber
  pointConfigs.value = []
  try {
    const [dev, config] = await Promise.all([getDevice(devNumber), listPoints(devNumber)])
    if (props.devNumber === devNumber) { device.value = dev; pointConfigs.value = config }
  } catch {
    if (props.devNumber === devNumber) deviceError.value = '输入输出通道配置加载失败，保留原始点位显示'
  }
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

function ageLabel(ts: string): string {
  const time = Date.parse(ts)
  if (!Number.isFinite(time)) return '暂无采集时间'
  const diff = Math.max(0, (now.value - time) / 1000)
  if (diff < 60) return `${Math.floor(diff)} 秒前`
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  return `${Math.floor(diff / 3600)} 小时前`
}

function sampleIsStale(p: RealtimePoint): boolean {
  const sampleTime = Date.parse(p.ts)
  const sampleAge = Math.max(now.value, Date.now()) - sampleTime
  // Match the serial availability grace: three polling periods, at least 10s.
  // HTTP success can return an old DB snapshot; only the sample time proves freshness.
  return !validSample(p) || p.value === null || sampleAge < 0 ||
    sampleAge >= Math.max(10000, 3 * intervalMs.value)
}
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
        <span>采集间隔 {{ intervalMs / 1000 }} 秒</span>
      </div>
      <nav class="tabs">
        <button @click="router.push(`/devices/${devNumber}`)">实时</button>
        <button @click="reloadSnapshot">刷新</button>
        <button @click="router.push(`/devices/${devNumber}/history`)">历史</button>
        <button @click="router.push(`/devices/${devNumber}/control`)">控制</button>
        <button @click="router.push(`/devices/${devNumber}/points`)">点位配置</button>
        <button v-permission="['Administrators','GroupCompany','Company']" @click="router.push(`/devices/${devNumber}/edit`)">编辑</button>
        <button v-permission="['Administrators','GroupCompany','Company']" @click="toggleEnabled">
          {{ device?.is_enabled === false ? '启用' : '停用' }}
        </button>
      </nav>
    </header>

    <p v-if="deviceError" role="status">{{ deviceError }}</p>
    <p v-if="refreshError" role="status">{{ refreshError }}</p>
    <LoadingSkeleton v-if="snapshotLoader.isPending.value && points.length === 0" :lines="5" />

    <DoControlPanel
      v-if="showDigitalPanel" :key="devNumber" :dev-number="devNumber"
      :can-control="canControl" show-inputs :input-sample="inputSample" :output-sample="outputSample"
      :stale-after-ms="Math.max(10000, 3 * intervalMs)" @configuration-changed="reloadDigitalConfig"
    />

    <div v-if="points.length" class="points-grid">
      <div
        v-for="p in remainingPoints"
        :key="p.point_id"
        class="point-card"
        :data-stale="sampleIsStale(p)"
        @click="openHistory(p)"
      >
        <div class="p-name">{{ p.point_name ?? `点位 ${p.point_id}` }}</div>
        <div class="p-value">
          {{ formatPointValue(p.value, p.display_bits) }}<span class="unit">{{ p.unit ?? '' }}</span>
        </div>
        <div v-if="p.display_bits" class="p-channels">
          <span v-for="channel in digitalChannels(p.value, p.display_bits)" :key="channel.number" :data-high="sampleIsStale(p) ? undefined : channel.high">
            {{ sampleIsStale(p) ? '上次' : '' }}第{{ channel.number }}路：{{ channel.high ? '高电平' : '低电平' }}
          </span>
          <small>从右往左为第1路、第2路…；1=高，0=低</small>
        </div>
        <div v-if="sampleIsStale(p)" class="p-stale" role="status">数据未更新，等待设备新采样</div>
        <div class="p-ts" :data-ts="p.ts">{{ ageLabel(p.ts) }}</div>
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
.p-channels { display: flex; flex-direction: column; gap: 4px; margin-bottom: 8px; font-size: 12px; }
.p-channels [data-high='true'] { color: #156e32; font-weight: 600; }
.p-channels small { color: var(--color-text-secondary); font-size: 11px; }
.point-card[data-stale='true'] { background: #f8fafc; border-color: #cbd5e1; }
.point-card[data-stale='true'] .p-value, .point-card[data-stale='true'] .p-channels { color: #64748b; }
.p-stale { color: #92400e; font-size: 12px; margin: 6px 0; }
</style>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useDevicesStore } from '@/stores/devices'
import { useAlarmsStore } from '@/stores/alarms'
import { useRecent } from '@/composables/useRecent'
import { useAsync } from '@/composables/useAsync'
import { listDevices } from '@/api/devices'
import LoadingSkeleton from '@/components/LoadingSkeleton.vue'

const router = useRouter()
const auth = useAuthStore()
const devices = useDevicesStore()
const alarms = useAlarmsStore()
const recentDevices = useRecent<string>('devices', 5)

const loader = useAsync(listDevices)

onMounted(async () => {
  if (devices.list.length === 0) {
    const list = await loader.run()
    devices.setList(list.map((x) => ({ ...x })))
  }
})

const stats = computed(() => {
  const total = devices.list.length
  const online = devices.list.filter((d) => d.state === 'online').length
  const offline = devices.list.filter((d) => d.state === 'offline').length
  const warning = devices.list.filter((d) => d.state === 'warning').length
  return { total, online, offline, warning }
})

const recentAlarms = computed(() => alarms.feed.slice(0, 5))

function openDevice(devNumber: string): void {
  recentDevices.push(devNumber)
  router.push(`/devices/${devNumber}`)
}
</script>

<template>
  <section class="dashboard">
    <div class="welcome">
      <h2>欢迎，<span data-testid="welcome-username">{{ auth.user?.user_name }}</span></h2>
      <p class="sub">{{ auth.user?.authority }} · {{ auth.user?.usr_group }}</p>
    </div>

    <div class="cards">
      <div class="card" data-testid="stat-total">
        <span class="num">{{ stats.total }}</span>
        <span class="label">设备总数</span>
      </div>
      <div class="card online" data-testid="stat-online">
        <span class="num">{{ stats.online }}</span>
        <span class="label">在线</span>
      </div>
      <div class="card offline" data-testid="stat-offline">
        <span class="num">{{ stats.offline }}</span>
        <span class="label">离线</span>
      </div>
      <div class="card warning" data-testid="stat-warning">
        <span class="num">{{ stats.warning }}</span>
        <span class="label">告警中</span>
      </div>
    </div>

    <div class="grid">
      <section class="panel">
        <h3>最近告警</h3>
        <LoadingSkeleton v-if="loader.isPending.value" :lines="3" />
        <ul v-else-if="recentAlarms.length" class="alarm-list">
          <li
            v-for="a in recentAlarms"
            :key="a.event_id"
            @click="openDevice(a.dev_number)"
          >
            <div class="a-head">
              <span class="a-name">{{ a.alarm_name }}</span>
              <span class="a-ts">{{ new Date(a.ts).toLocaleString() }}</span>
            </div>
            <div class="a-body">
              {{ a.dev_number }} — 当前 {{ a.value }} / 阈值 {{ a.limit }}
            </div>
          </li>
        </ul>
        <p v-else class="empty-inline">暂无告警</p>
      </section>

      <section class="panel">
        <h3>最近访问设备</h3>
        <ul v-if="recentDevices.items.value.length" class="recent-list">
          <li v-for="d in recentDevices.items.value" :key="d" @click="openDevice(d)">
            <code>{{ d }}</code>
          </li>
        </ul>
        <p v-else class="empty-inline">暂无访问记录</p>
      </section>
    </div>
  </section>
</template>

<style scoped>
.dashboard { display: flex; flex-direction: column; gap: 16px; }
.welcome { background: #fff; padding: 16px; border-radius: 6px; }
.welcome h2 { font-size: 18px; }
.sub { color: var(--color-text-secondary); font-size: 13px; margin-top: 4px; }
.cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.card { background: #fff; padding: 16px; border-radius: 6px; display: flex; flex-direction: column; gap: 4px; border-left: 4px solid var(--color-primary); }
.card.online { border-color: var(--color-success); }
.card.offline { border-color: #999; }
.card.warning { border-color: var(--color-warning); }
.card .num { font-size: 24px; font-weight: 600; }
.card .label { font-size: 13px; color: var(--color-text-secondary); }
.grid { display: grid; grid-template-columns: 2fr 1fr; gap: 12px; }
.panel { background: #fff; padding: 16px; border-radius: 6px; }
.panel h3 { font-size: 14px; margin-bottom: 12px; }
.alarm-list, .recent-list { list-style: none; display: flex; flex-direction: column; gap: 8px; }
.alarm-list li { padding: 8px; border: 1px solid #eee; border-radius: 4px; cursor: pointer; }
.alarm-list li:hover { background: #f7f7f7; }
.a-head { display: flex; justify-content: space-between; font-size: 13px; font-weight: 500; }
.a-ts { color: var(--color-text-secondary); font-size: 11px; }
.a-body { color: var(--color-text-secondary); font-size: 12px; margin-top: 2px; }
.recent-list li { padding: 6px; border-bottom: 1px dashed #eee; cursor: pointer; font-size: 13px; }
.empty-inline { color: var(--color-text-secondary); font-size: 13px; }
@media (max-width: 768px) { .cards { grid-template-columns: repeat(2, 1fr); } .grid { grid-template-columns: 1fr; } }
</style>

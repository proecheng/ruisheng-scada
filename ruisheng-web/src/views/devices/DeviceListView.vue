<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { listDevices, deleteDevice, setDeviceEnabled, type Device } from '@/api/devices'
import { useDevicesStore } from '@/stores/devices'
import { useToast } from '@/composables/useToast'
import { useAsync } from '@/composables/useAsync'
import LoadingSkeleton from '@/components/LoadingSkeleton.vue'
import EmptyState from '@/components/EmptyState.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'

const router = useRouter()
const devicesStore = useDevicesStore()
const toast = useToast()

const query = ref('')
const companyFilter = ref('')
const stateFilter = ref<'' | 'online' | 'offline' | 'warning'>('')
const loader = useAsync(listDevices)

const deleteTarget = ref<Device | null>(null)
const showDeleteDialog = ref(false)

onMounted(async () => {
  const list = await loader.run()
  devicesStore.setList(
    list.map((d) => ({
      dev_number: d.dev_number,
      dev_name: d.dev_name,
      state: d.state,
      company: d.company,
      department: d.department,
    })),
  )
})

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  return (loader.data.value ?? []).filter((d) => {
    if (stateFilter.value && d.state !== stateFilter.value) return false
    if (companyFilter.value && d.company !== companyFilter.value) return false
    if (q && !(`${d.dev_number} ${d.dev_name}`.toLowerCase().includes(q))) return false
    return true
  })
})

const companies = computed(() => {
  const set = new Set<string>()
  for (const d of loader.data.value ?? []) {
    if (d.company) set.add(d.company)
  }
  return [...set]
})

function openDetail(d: Device): void {
  router.push(`/devices/${d.dev_number}`)
}

function askDelete(d: Device): void {
  deleteTarget.value = d
  showDeleteDialog.value = true
}

async function confirmDelete(): Promise<void> {
  if (!deleteTarget.value) return
  const dev = deleteTarget.value
  try {
    await deleteDevice(dev.dev_number)
    toast.success(`已删除设备 ${dev.dev_number}`)
    const list = await loader.run()
    devicesStore.setList(list.map((x) => ({ ...x })))
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '删除失败')
  } finally {
    deleteTarget.value = null
  }
}

async function toggleEnabled(d: Device): Promise<void> {
  try {
    const updated = await setDeviceEnabled(d.dev_number, !(d.is_enabled ?? true))
    const list = (loader.data.value ?? []).map((x) => (x.dev_number === updated.dev_number ? updated : x))
    loader.data.value = list
    devicesStore.setList(list.map((x) => ({ ...x })))
    toast.success(updated.is_enabled ? '设备已启用' : '设备已停用')
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '切换失败')
  }
}
</script>

<template>
  <section class="device-list">
    <header class="toolbar">
      <h2>设备列表</h2>
      <div class="filters">
        <input v-model="query" data-testid="device-search" class="search" type="text" placeholder="搜索设备号/名称…" />
        <select v-model="companyFilter">
          <option value="">所有公司</option>
          <option v-for="c in companies" :key="c" :value="c">{{ c }}</option>
        </select>
        <select v-model="stateFilter" data-testid="device-state-filter">
          <option value="">所有状态</option>
          <option value="online">在线</option>
          <option value="offline">离线</option>
          <option value="warning">告警</option>
        </select>
        <button
          v-permission="['Administrators','GroupCompany','Company']"
          class="add"
          @click="router.push('/devices/new')"
        >
          + 添加设备
        </button>
      </div>
    </header>

    <LoadingSkeleton v-if="loader.isPending.value" :lines="6" />

    <EmptyState
      v-else-if="filtered.length === 0"
      icon="🖥"
      title="暂无设备"
      description="该筛选下没有设备，可以调整筛选或添加新设备"
      action-label="添加设备"
      @action="router.push('/devices/new')"
    />

    <table v-else class="device-table">
      <thead>
        <tr>
          <th>设备号</th>
          <th>名称</th>
          <th>启用</th>
          <th>状态</th>
          <th>通信</th>
          <th>公司</th>
          <th>部门</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="d in filtered" :key="d.dev_number" data-testid="device-row" @click="openDetail(d)">
          <td><code data-testid="device-number">{{ d.dev_number }}</code></td>
          <td>{{ d.dev_name }}</td>
          <td>{{ d.is_enabled === false ? '停用' : '启用' }}</td>
          <td>
            <span class="pill" :data-state="d.state">{{
              d.state === 'online' ? '在线' : d.state === 'offline' ? '离线' : '告警'
            }}</span>
          </td>
          <td>{{ d.transport_type === 'serial' ? `串口 ${d.serial_port ?? '—'}` : `TCP ${d.dev_ip ?? '不限 IP'}` }}</td>
          <td>{{ d.company ?? '—' }}</td>
          <td>{{ d.department ?? '—' }}</td>
          <td @click.stop>
            <button
              v-permission="['Administrators','GroupCompany','Company']"
              @click="router.push(`/devices/${d.dev_number}/edit`)"
            >
              编辑
            </button>
            <button
              v-permission="['Administrators','GroupCompany','Company']"
              @click="toggleEnabled(d)"
            >
              {{ d.is_enabled === false ? '启用' : '停用' }}
            </button>
            <button
              v-permission="['Administrators','GroupCompany','Company']"
              class="danger"
              @click="askDelete(d)"
            >
              删除
            </button>
          </td>
        </tr>
      </tbody>
    </table>

    <ConfirmDialog
      v-model="showDeleteDialog"
      :message="`确认删除设备 ${deleteTarget?.dev_number}？`"
      danger
      :type-to-confirm="deleteTarget?.dev_number ?? undefined"
      @confirm="confirmDelete"
    />
  </section>
</template>

<style scoped>
.device-list { background: #fff; padding: 16px; border-radius: 6px; }
.toolbar { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 12px; margin-bottom: 16px; }
.filters { display: flex; gap: 8px; flex-wrap: wrap; }
.search, select { padding: 6px 10px; border: 1px solid #ccc; border-radius: 4px; font-size: 13px; }
.add { background: var(--color-primary); color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; }
.device-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.device-table th, .device-table td { padding: 8px 10px; border-bottom: 1px solid #eee; text-align: left; }
.device-table tbody tr { cursor: pointer; }
.device-table tbody tr:hover { background: #f7f7f7; }
.pill { padding: 2px 8px; border-radius: 10px; font-size: 11px; }
.pill[data-state='online'] { background: #e8f5e9; color: var(--color-success); }
.pill[data-state='offline'] { background: #f5f5f5; color: var(--color-text-secondary); }
.pill[data-state='warning'] { background: #fff3e0; color: var(--color-warning); }
.danger { background: none; border: 1px solid var(--color-error); color: var(--color-error); padding: 3px 8px; border-radius: 3px; cursor: pointer; font-size: 12px; }
</style>

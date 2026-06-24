<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  listAlarmConfigs,
  createAlarmConfig,
  updateAlarmConfig,
  deleteAlarmConfig,
  type AlarmConfig,
  type AlarmType,
} from '@/api/alarms'
import { listPoints, type PointConfig } from '@/api/points'
import { useAsync } from '@/composables/useAsync'
import { useToast } from '@/composables/useToast'
import LoadingSkeleton from '@/components/LoadingSkeleton.vue'
import EmptyState from '@/components/EmptyState.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'

const props = defineProps<{ devNumber: string }>()
const router = useRouter()
const toast = useToast()

const loader = useAsync(() => listAlarmConfigs(props.devNumber))
const cfgs = ref<AlarmConfig[]>([])
const pointsLoader = useAsync(() => listPoints(props.devNumber))
const points = ref<PointConfig[]>([])
const isLoading = computed(() => loader.isPending.value || pointsLoader.isPending.value)
const canAddConfig = computed(() => points.value.length > 0 && !pointsLoader.isPending.value)

const editing = ref<AlarmConfig | null>(null)
const isNew = ref(false)
const deleteTarget = ref<AlarmConfig | null>(null)
const showDeleteDialog = ref(false)

const ALL_CHANNELS = ['wechat', 'sms', 'voice', 'email'] as const

async function reload(): Promise<void> {
  const [loadedCfgs, loadedPoints] = await Promise.all([loader.run(), pointsLoader.run()])
  cfgs.value = loadedCfgs
  points.value = loadedPoints
}

onMounted(() => reload())

function startNew(): void {
  if (pointsLoader.isPending.value) return
  if (points.value.length === 0) {
    toast.error('请先配置点位，再新增告警阈值')
    return
  }
  editing.value = {
    cfg_id: 0,
    point_id: points.value[0]?.point_id ?? 0,
    alarm_name: '',
    alarm_type: '>',
    limit: 0,
    severity: 'warning',
    channels: ['wechat'],
  }
  isNew.value = true
}

function goToPointConfig(): void {
  router.push(`/devices/${props.devNumber}/points`)
}

function pointLabel(pointId: number): string {
  const point = points.value.find((p) => p.point_id === pointId)
  if (!point) return `点位 ID ${pointId}`
  return `${point.point_name} (ID ${point.point_id}, FC${point.fun_code} 地址 ${point.register_address})`
}

function startEdit(c: AlarmConfig): void {
  editing.value = { ...c, channels: [...c.channels] }
  isNew.value = false
}

async function save(): Promise<void> {
  if (!editing.value) return
  try {
    if (isNew.value) {
      const { cfg_id: _cfg_id, ...payload } = editing.value
      await createAlarmConfig(props.devNumber, payload)
      toast.success('已新增')
    } else {
      await updateAlarmConfig(props.devNumber, editing.value.cfg_id, editing.value)
      toast.success('已保存')
    }
    editing.value = null
    await reload()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '保存失败')
  }
}

function askDelete(c: AlarmConfig): void {
  deleteTarget.value = c
  showDeleteDialog.value = true
}

async function confirmDelete(): Promise<void> {
  if (!deleteTarget.value) return
  try {
    await deleteAlarmConfig(props.devNumber, deleteTarget.value.cfg_id)
    toast.success('已删除')
    await reload()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '删除失败')
  } finally {
    deleteTarget.value = null
  }
}

function toggleChannel(ch: string): void {
  if (!editing.value) return
  const i = editing.value.channels.indexOf(ch)
  if (i >= 0) editing.value.channels.splice(i, 1)
  else editing.value.channels.push(ch)
}

const ALARM_TYPES: AlarmType[] = ['>', '<', '=', '!=', 'LX']
</script>

<template>
  <section class="alarm-config">
    <header>
      <button class="back" @click="router.back()">← 返回</button>
      <h2>{{ devNumber }} — 告警阈值</h2>
      <button class="add" :disabled="!canAddConfig" @click="startNew">+ 新增阈值</button>
    </header>

    <LoadingSkeleton v-if="isLoading" :lines="3" />
    <EmptyState
      v-else-if="points.length === 0"
      title="尚未配置点位"
      description="告警阈值需要先绑定设备点位。"
      action-label="去配置点位"
      @action="goToPointConfig"
    />
    <EmptyState v-else-if="cfgs.length === 0" title="尚无告警规则" action-label="新增阈值" @action="startNew" />
    <table v-else class="cfg-table">
      <thead>
        <tr>
          <th>绑定点位</th><th>规则名</th><th>类型</th><th>阈值</th><th>严重度</th><th>通道</th><th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="c in cfgs" :key="c.cfg_id">
          <td>{{ pointLabel(c.point_id) }}</td>
          <td>{{ c.alarm_name }}</td>
          <td>{{ c.alarm_type }}</td>
          <td>{{ c.limit }}</td>
          <td><span class="pill" :data-sev="c.severity">{{ c.severity }}</span></td>
          <td>{{ c.channels.join('、') }}</td>
          <td>
            <button @click="startEdit(c)">编辑</button>
            <button class="danger" @click="askDelete(c)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="editing" class="drawer">
      <h3>{{ isNew ? '新增规则' : `编辑规则 ${editing.cfg_id}` }}</h3>
      <form @submit.prevent="save">
        <label>
          绑定点位
          <select v-model.number="editing.point_id" required>
            <option v-for="p in points" :key="p.point_id" :value="p.point_id">
              {{ p.point_name }}（ID {{ p.point_id }} / FC{{ p.fun_code }} 地址 {{ p.register_address }}）
            </option>
          </select>
        </label>
        <label>规则名 <input v-model="editing.alarm_name" type="text" required /></label>
        <label>
          比较类型
          <select v-model="editing.alarm_type">
            <option v-for="t in ALARM_TYPES" :key="t" :value="t">{{ t }}</option>
          </select>
        </label>
        <label>阈值 <input v-model.number="editing.limit" type="number" step="any" /></label>
        <label v-if="editing.alarm_type === 'LX'">
          关联点位（联锁）
          <select v-model.number="editing.relation_point_id">
            <option :value="undefined">不关联</option>
            <option v-for="p in points" :key="p.point_id" :value="p.point_id">
              {{ p.point_name }}（ID {{ p.point_id }}）
            </option>
          </select>
        </label>
        <label>
          严重度
          <select v-model="editing.severity">
            <option value="info">info</option>
            <option value="warning">warning</option>
            <option value="critical">critical</option>
          </select>
        </label>
        <div class="channels">
          <span>通知通道</span>
          <label v-for="ch in ALL_CHANNELS" :key="ch">
            <input
              type="checkbox"
              :checked="editing.channels.includes(ch)"
              @change="toggleChannel(ch)"
            />
            {{ ch }}
          </label>
        </div>
        <div class="actions">
          <button type="button" @click="editing = null">取消</button>
          <button type="submit" class="primary">保存</button>
        </div>
      </form>
    </div>

    <ConfirmDialog
      v-model="showDeleteDialog"
      :message="`确认删除规则 ${deleteTarget?.alarm_name}？`"
      danger
      @confirm="confirmDelete"
    />
  </section>
</template>

<style scoped>
.alarm-config { background: #fff; padding: 16px; border-radius: 6px; }
header { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.back { background: none; border: 1px solid #ccc; padding: 4px 10px; border-radius: 4px; cursor: pointer; }
h2 { flex: 1; font-size: 18px; }
.add { background: var(--color-primary); color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; }
.add:disabled { opacity: 0.45; cursor: not-allowed; }
.cfg-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.cfg-table th, .cfg-table td { padding: 8px 10px; border-bottom: 1px solid #eee; text-align: left; }
.cfg-table button { margin-right: 6px; padding: 3px 8px; border: 1px solid #ccc; background: #fff; border-radius: 3px; cursor: pointer; font-size: 12px; }
.cfg-table .danger { border-color: var(--color-error); color: var(--color-error); }
.pill { padding: 2px 8px; border-radius: 10px; font-size: 11px; }
.pill[data-sev='info'] { background: #e3f2fd; color: var(--color-primary); }
.pill[data-sev='warning'] { background: #fff3e0; color: var(--color-warning); }
.pill[data-sev='critical'] { background: #ffebee; color: var(--color-error); }
.drawer { position: fixed; right: 0; top: 0; height: 100vh; width: min(420px, 100vw); background: #fff; box-shadow: -2px 0 8px rgba(0,0,0,0.15); padding: 20px; overflow-y: auto; z-index: 500; }
.drawer form { display: flex; flex-direction: column; gap: 12px; }
.drawer label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.drawer input, .drawer select { padding: 6px 10px; border: 1px solid #ccc; border-radius: 4px; }
.channels { display: flex; flex-wrap: wrap; gap: 10px; font-size: 13px; align-items: center; }
.channels label { flex-direction: row !important; align-items: center; gap: 4px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.actions button { padding: 6px 14px; border: 1px solid #ccc; border-radius: 4px; cursor: pointer; }
.primary { background: var(--color-primary); color: white; border-color: var(--color-primary); }
</style>

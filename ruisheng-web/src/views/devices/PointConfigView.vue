<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  listPoints,
  createPoint,
  updatePoint,
  deletePoint,
  type PointConfig,
} from '@/api/points'
import { useAsync } from '@/composables/useAsync'
import { useToast } from '@/composables/useToast'
import LoadingSkeleton from '@/components/LoadingSkeleton.vue'
import EmptyState from '@/components/EmptyState.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'

const props = defineProps<{ devNumber: string }>()
const router = useRouter()
const toast = useToast()

const loader = useAsync(() => listPoints(props.devNumber))
const points = ref<PointConfig[]>([])

const editing = ref<PointConfig | null>(null)
const isNew = ref(false)
const deleteTarget = ref<PointConfig | null>(null)
const showDeleteDialog = ref(false)

const functionLabels: Record<PointConfig['fun_code'], string> = {
  1: 'FC1 线圈',
  2: 'FC2 离散输入',
  3: 'FC3 保持寄存器',
  4: 'FC4 输入寄存器',
}

const requiresRegisterBit = computed(
  () => editing.value?.data_type === 'bit' && editing.value.fun_code !== 1 && editing.value.fun_code !== 2,
)

async function reload(): Promise<void> {
  points.value = await loader.run()
}

onMounted(() => reload())

function startNew(): void {
  editing.value = {
    point_id: 0,
    point_name: '',
    register_address: 0,
    fun_code: 3,
    dev_addr: 1,
    r_bit: null,
    data_type: '字',
    raw_ratio: 1,
    raw_offset: 0,
    ratio: 1,
    offset: 0,
    unit: '',
    precision: 2,
    min_value: null,
    max_value: null,
    show: true,
  }
  isNew.value = true
}

function startEdit(p: PointConfig): void {
  editing.value = { ...p }
  isNew.value = false
}

async function save(): Promise<void> {
  if (!editing.value) return
  try {
    normalizeEditing()
    if (isNew.value) {
      await createPoint(props.devNumber, editing.value)
      toast.success('已新增')
    } else {
      await updatePoint(props.devNumber, editing.value.point_id, editing.value)
      toast.success('已保存')
    }
    editing.value = null
    await reload()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '保存失败')
  }
}

function normalizeEditing(): void {
  if (!editing.value) return
  if (editing.value.fun_code === 1 || editing.value.fun_code === 2) {
    editing.value.data_type = 'bit'
    editing.value.r_bit = null
  }
  if (editing.value.data_type !== 'bit') {
    editing.value.r_bit = null
  }
  if (requiresRegisterBit.value && (editing.value.r_bit === null || editing.value.r_bit === undefined)) {
    throw new Error('寄存器 bit 点必须填写位号 0-15')
  }
  if (
    editing.value.min_value !== null &&
    editing.value.min_value !== undefined &&
    editing.value.max_value !== null &&
    editing.value.max_value !== undefined &&
    editing.value.min_value > editing.value.max_value
  ) {
    throw new Error('最小值不能大于最大值')
  }
}

function onFunCodeChange(): void {
  if (!editing.value) return
  if (editing.value.fun_code === 1 || editing.value.fun_code === 2) {
    editing.value.data_type = 'bit'
    editing.value.r_bit = null
  } else if (editing.value.data_type === 'bit') {
    editing.value.r_bit ??= 0
  }
}

function onDataTypeChange(): void {
  if (!editing.value) return
  if (editing.value.data_type === 'bit' && editing.value.fun_code !== 1 && editing.value.fun_code !== 2) {
    editing.value.r_bit ??= 0
  } else {
    editing.value.r_bit = null
  }
}

function askDelete(p: PointConfig): void {
  deleteTarget.value = p
  showDeleteDialog.value = true
}

async function confirmDelete(): Promise<void> {
  if (!deleteTarget.value) return
  try {
    await deletePoint(props.devNumber, deleteTarget.value.point_id)
    toast.success('已删除')
    await reload()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '删除失败')
  } finally {
    deleteTarget.value = null
  }
}
</script>

<template>
  <section class="point-config">
    <header>
      <button class="back" @click="router.back()">← 返回</button>
      <h2>{{ devNumber }} — 点位配置</h2>
      <button class="add" @click="startNew">+ 新增点位</button>
    </header>

    <LoadingSkeleton v-if="loader.isPending.value" :lines="4" />
    <EmptyState
      v-else-if="points.length === 0"
      title="尚未配置点位"
      description="添加第一个点位以解析设备上报的原始值"
      action-label="新增点位"
      @action="startNew"
    />
    <table v-else class="point-table">
      <thead>
        <tr>
          <th>点位 ID</th>
          <th>名称</th>
          <th>寄存器类型</th>
          <th>地址</th>
          <th>数据类型</th>
          <th>位号</th>
          <th>倍率/偏移</th>
          <th>单位</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="p in points" :key="p.point_id">
          <td>{{ p.point_id }}</td>
          <td>{{ p.point_name }}</td>
          <td>{{ functionLabels[p.fun_code] }}</td>
          <td>{{ p.register_address }}</td>
          <td>{{ p.data_type }}</td>
          <td>{{ p.r_bit ?? '—' }}</td>
          <td>{{ p.raw_ratio }} / {{ p.raw_offset }}；{{ p.ratio }} / {{ p.offset }}</td>
          <td>{{ p.unit ?? '—' }}</td>
          <td>
            <button @click="startEdit(p)">编辑</button>
            <button class="danger" @click="askDelete(p)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="editing" class="drawer">
      <h3>{{ isNew ? '新增点位' : `编辑点位 ${editing.point_id}` }}</h3>
      <form @submit.prevent="save">
        <label v-if="!isNew">
          点位 ID
          <input :value="editing.point_id" type="number" readonly />
        </label>
        <label>
          点位名称
          <input v-model="editing.point_name" type="text" required />
        </label>
        <label>
          寄存器类型
          <select v-model.number="editing.fun_code" @change="onFunCodeChange">
            <option :value="1">FC1 线圈</option>
            <option :value="2">FC2 离散输入</option>
            <option :value="3">FC3 保持寄存器</option>
            <option :value="4">FC4 输入寄存器</option>
          </select>
        </label>
        <label>
          寄存器/线圈地址
          <input v-model.number="editing.register_address" type="number" min="0" max="65535" required />
        </label>
        <label>
          从站地址
          <input v-model.number="editing.dev_addr" type="number" min="1" max="247" required />
        </label>
        <label>
          数据类型
          <select v-model="editing.data_type" :disabled="editing.fun_code === 1 || editing.fun_code === 2" @change="onDataTypeChange">
            <option value="字">字</option>
            <option value="双字">双字</option>
            <option value="bit">bit</option>
          </select>
        </label>
        <label v-if="requiresRegisterBit">
          位号
          <input v-model.number="editing.r_bit" type="number" min="0" max="15" required />
        </label>
        <label>
          原始倍率
          <input v-model.number="editing.raw_ratio" type="number" step="0.0001" />
        </label>
        <label>
          原始偏移
          <input v-model.number="editing.raw_offset" type="number" step="0.0001" />
        </label>
        <label>
          显示倍率
          <input v-model.number="editing.ratio" type="number" step="0.0001" />
        </label>
        <label>
          显示偏移
          <input v-model.number="editing.offset" type="number" step="0.0001" />
        </label>
        <label>
          单位
          <input v-model="editing.unit" type="text" />
        </label>
        <label>
          最小值
          <input v-model.number="editing.min_value" type="number" step="0.0001" />
        </label>
        <label>
          最大值
          <input v-model.number="editing.max_value" type="number" step="0.0001" />
        </label>
        <label class="checkbox">
          <input v-model="editing.show" type="checkbox" />
          显示
        </label>
        <div class="actions">
          <button type="button" @click="editing = null">取消</button>
          <button type="submit" class="primary">保存</button>
        </div>
      </form>
    </div>

    <ConfirmDialog
      v-model="showDeleteDialog"
      :message="`确认删除点位 ${deleteTarget?.point_id} (${deleteTarget?.point_name})？`"
      danger
      @confirm="confirmDelete"
    />
  </section>
</template>

<style scoped>
.point-config { background: #fff; padding: 16px; border-radius: 6px; }
header { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.back { background: none; border: 1px solid #ccc; padding: 4px 10px; border-radius: 4px; cursor: pointer; }
h2 { flex: 1; font-size: 18px; }
.add { background: var(--color-primary); color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; }
.point-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.point-table th, .point-table td { padding: 8px 10px; border-bottom: 1px solid #eee; text-align: left; }
.point-table button { margin-right: 6px; padding: 3px 8px; border: 1px solid #ccc; background: #fff; border-radius: 3px; cursor: pointer; font-size: 12px; }
.point-table .danger { border-color: var(--color-error); color: var(--color-error); }
.drawer {
  position: fixed; right: 0; top: 0; height: 100vh; width: min(400px, 100vw);
  background: #fff; box-shadow: -2px 0 8px rgba(0,0,0,0.15); padding: 20px; overflow-y: auto; z-index: 500;
}
.drawer h3 { font-size: 16px; margin-bottom: 16px; }
.drawer form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.drawer label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.drawer input, .drawer select { padding: 6px 10px; border: 1px solid #ccc; border-radius: 4px; background: #fff; }
.drawer .checkbox { flex-direction: row; align-items: center; }
.drawer .checkbox input { width: auto; }
.actions { grid-column: 1 / -1; display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.actions button { padding: 6px 14px; border: 1px solid #ccc; border-radius: 4px; cursor: pointer; }
.primary { background: var(--color-primary); color: white; border-color: var(--color-primary); }
@media (max-width: 640px) {
  .drawer form { grid-template-columns: 1fr; }
}
</style>

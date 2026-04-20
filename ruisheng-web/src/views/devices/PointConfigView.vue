<script setup lang="ts">
import { ref, onMounted } from 'vue'
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

async function reload(): Promise<void> {
  points.value = await loader.run()
}

onMounted(() => reload())

function startNew(): void {
  editing.value = { point_id: 0, point_name: '', ratio: 1, offset: 0, unit: '', precision: 2 }
  isNew.value = true
}

function startEdit(p: PointConfig): void {
  editing.value = { ...p }
  isNew.value = false
}

async function save(): Promise<void> {
  if (!editing.value) return
  try {
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
          <th>ID</th><th>名称</th><th>Ratio</th><th>Offset</th><th>单位</th><th>精度</th><th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="p in points" :key="p.point_id">
          <td>{{ p.point_id }}</td>
          <td>{{ p.point_name }}</td>
          <td>{{ p.ratio }}</td>
          <td>{{ p.offset }}</td>
          <td>{{ p.unit ?? '—' }}</td>
          <td>{{ p.precision ?? 2 }}</td>
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
        <label v-if="isNew">
          Point ID
          <input v-model.number="editing.point_id" type="number" required />
        </label>
        <label>名称 <input v-model="editing.point_name" type="text" required /></label>
        <label>Ratio <input v-model.number="editing.ratio" type="number" step="0.0001" /></label>
        <label>Offset <input v-model.number="editing.offset" type="number" step="0.0001" /></label>
        <label>单位 <input v-model="editing.unit" type="text" /></label>
        <label>精度 <input v-model.number="editing.precision" type="number" min="0" max="6" /></label>
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
.drawer form { display: flex; flex-direction: column; gap: 12px; }
.drawer label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.drawer input { padding: 6px 10px; border: 1px solid #ccc; border-radius: 4px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.actions button { padding: 6px 14px; border: 1px solid #ccc; border-radius: 4px; cursor: pointer; }
.primary { background: var(--color-primary); color: white; border-color: var(--color-primary); }
</style>

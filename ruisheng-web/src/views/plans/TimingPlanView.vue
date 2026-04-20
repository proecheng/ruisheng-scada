<script setup lang="ts">
import { ref, onMounted } from 'vue'
import {
  listTimingPlans,
  upsertTimingPlan,
  deleteTimingPlan,
  type TimingPlan,
} from '@/api/plans'
import { useAsync } from '@/composables/useAsync'
import { useToast } from '@/composables/useToast'
import LoadingSkeleton from '@/components/LoadingSkeleton.vue'
import EmptyState from '@/components/EmptyState.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'

const toast = useToast()
const loader = useAsync(listTimingPlans)
const plans = ref<TimingPlan[]>([])

const editing = ref<TimingPlan | null>(null)
const isNew = ref(false)
const deleteTarget = ref<TimingPlan | null>(null)
const showDeleteDialog = ref(false)

async function reload(): Promise<void> {
  plans.value = await loader.run()
}

onMounted(() => reload())

function startNew(): void {
  editing.value = {
    id: 0,
    dev_number: '',
    plan_name: '',
    cron: '0 0 * * *',
    action: 'start',
    enabled: true,
  }
  isNew.value = true
}

function startEdit(p: TimingPlan): void {
  editing.value = { ...p }
  isNew.value = false
}

async function save(): Promise<void> {
  if (!editing.value) return
  try {
    await upsertTimingPlan(editing.value)
    toast.success('已保存')
    editing.value = null
    await reload()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '保存失败')
  }
}

async function toggleEnabled(p: TimingPlan): Promise<void> {
  try {
    await upsertTimingPlan({ ...p, enabled: !p.enabled })
    toast.success(p.enabled ? '已停用' : '已启用')
    await reload()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '切换失败')
  }
}

function askDelete(p: TimingPlan): void {
  deleteTarget.value = p
  showDeleteDialog.value = true
}

async function confirmDelete(): Promise<void> {
  if (!deleteTarget.value) return
  try {
    await deleteTimingPlan(deleteTarget.value.id)
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
  <section class="timing">
    <header>
      <h2>定时计划</h2>
      <button class="add" @click="startNew">+ 新增计划</button>
    </header>

    <LoadingSkeleton v-if="loader.isPending.value" :lines="3" />
    <EmptyState v-else-if="plans.length === 0" title="暂无定时计划" action-label="新增" @action="startNew" />
    <table v-else class="p-table">
      <thead>
        <tr>
          <th>启用</th><th>设备</th><th>计划名</th><th>Cron</th><th>动作</th><th>下次运行</th><th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="p in plans" :key="p.id">
          <td>
            <input type="checkbox" :checked="p.enabled" @change="toggleEnabled(p)" />
          </td>
          <td><code>{{ p.dev_number }}</code></td>
          <td>{{ p.plan_name }}</td>
          <td><code>{{ p.cron }}</code></td>
          <td>{{ p.action }}</td>
          <td>{{ p.next_run_at ? new Date(p.next_run_at).toLocaleString() : '—' }}</td>
          <td>
            <button @click="startEdit(p)">编辑</button>
            <button class="danger" @click="askDelete(p)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="editing" class="drawer">
      <h3>{{ isNew ? '新增定时计划' : `编辑计划 ${editing.id}` }}</h3>
      <form @submit.prevent="save">
        <label>设备号 <input v-model="editing.dev_number" type="text" required /></label>
        <label>计划名 <input v-model="editing.plan_name" type="text" required /></label>
        <label>
          Cron 表达式
          <input v-model="editing.cron" type="text" placeholder="分 时 日 月 周" required />
          <small>例：0 8 * * * = 每天 8:00</small>
        </label>
        <label>
          动作
          <select v-model="editing.action">
            <option value="start">启动</option>
            <option value="stop">停止</option>
            <option value="reset">复位</option>
          </select>
        </label>
        <label class="inline">
          <input v-model="editing.enabled" type="checkbox" />
          启用
        </label>
        <div class="actions">
          <button type="button" @click="editing = null">取消</button>
          <button type="submit" class="primary">保存</button>
        </div>
      </form>
    </div>

    <ConfirmDialog
      v-model="showDeleteDialog"
      :message="`确认删除计划 ${deleteTarget?.plan_name}？`"
      danger
      @confirm="confirmDelete"
    />
  </section>
</template>

<style scoped>
.timing { background: #fff; padding: 16px; border-radius: 6px; }
header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
h2 { font-size: 18px; }
.add { background: var(--color-primary); color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; }
.p-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.p-table th, .p-table td { padding: 8px 10px; border-bottom: 1px solid #eee; text-align: left; }
.p-table button { margin-right: 6px; padding: 3px 8px; border: 1px solid #ccc; background: #fff; border-radius: 3px; cursor: pointer; font-size: 12px; }
.p-table .danger { border-color: var(--color-error); color: var(--color-error); }
.drawer { position: fixed; right: 0; top: 0; height: 100vh; width: min(400px, 100vw); background: #fff; box-shadow: -2px 0 8px rgba(0,0,0,0.15); padding: 20px; overflow-y: auto; z-index: 500; }
.drawer form { display: flex; flex-direction: column; gap: 12px; }
.drawer label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.drawer label.inline { flex-direction: row !important; align-items: center; }
.drawer input, .drawer select { padding: 6px 10px; border: 1px solid #ccc; border-radius: 4px; }
.drawer small { color: var(--color-text-secondary); }
.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.actions button { padding: 6px 14px; border: 1px solid #ccc; border-radius: 4px; cursor: pointer; }
.primary { background: var(--color-primary); color: white; border-color: var(--color-primary); }
</style>

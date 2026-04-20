<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import {
  listMaintenancePlans,
  upsertMaintenancePlan,
  deleteMaintenancePlan,
  completeMaintenance,
  type MaintenancePlan,
} from '@/api/plans'
import { useAsync } from '@/composables/useAsync'
import { useToast } from '@/composables/useToast'
import { generateUlid } from '@/utils/ulid'
import { useAuthStore } from '@/stores/auth'
import LoadingSkeleton from '@/components/LoadingSkeleton.vue'
import EmptyState from '@/components/EmptyState.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'

const toast = useToast()
const auth = useAuthStore()
const loader = useAsync(listMaintenancePlans)
const plans = ref<MaintenancePlan[]>([])

const editing = ref<MaintenancePlan | null>(null)
const isNew = ref(false)
const completeTarget = ref<MaintenancePlan | null>(null)
const showCompleteDialog = ref(false)
const completeNote = ref('')

async function reload(): Promise<void> {
  plans.value = await loader.run()
}

onMounted(() => reload())

function startNew(): void {
  editing.value = {
    id: 0,
    dev_number: '',
    plan_name: '',
    interval_days: 30,
    next_due_at: new Date(Date.now() + 30 * 86400000).toISOString(),
    owner_user_name: auth.user?.user_name ?? '',
  }
  isNew.value = true
}

function startEdit(p: MaintenancePlan): void {
  editing.value = { ...p }
  isNew.value = false
}

async function save(): Promise<void> {
  if (!editing.value) return
  try {
    await upsertMaintenancePlan(editing.value)
    toast.success('已保存')
    editing.value = null
    await reload()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '保存失败')
  }
}

function askComplete(p: MaintenancePlan): void {
  completeTarget.value = p
  completeNote.value = ''
  showCompleteDialog.value = true
}

async function doComplete(): Promise<void> {
  if (!completeTarget.value) return
  const p = completeTarget.value
  try {
    await completeMaintenance(p.id, {
      action_uuid: generateUlid(),
      plan_id: p.id,
      dev_number: p.dev_number,
      note: completeNote.value,
    })
    toast.success('已记录保养，下次到期时间已推进')
    await reload()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '提交失败')
  } finally {
    completeTarget.value = null
  }
}

async function del(p: MaintenancePlan): Promise<void> {
  try {
    await deleteMaintenancePlan(p.id)
    toast.success('已删除')
    await reload()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '删除失败')
  }
}

const overdue = (p: MaintenancePlan) => new Date(p.next_due_at).getTime() < Date.now()
const daysUntil = (iso: string) => Math.floor((new Date(iso).getTime() - Date.now()) / 86400000)

const sorted = computed(() =>
  [...plans.value].sort((a, b) => new Date(a.next_due_at).getTime() - new Date(b.next_due_at).getTime()),
)
</script>

<template>
  <section class="maintenance">
    <header>
      <h2>保养计划</h2>
      <button class="add" @click="startNew">+ 新增计划</button>
    </header>

    <LoadingSkeleton v-if="loader.isPending.value" :lines="3" />
    <EmptyState v-else-if="sorted.length === 0" title="暂无保养计划" action-label="新增" @action="startNew" />
    <table v-else class="m-table">
      <thead>
        <tr>
          <th>设备</th><th>计划名</th><th>周期（天）</th><th>下次到期</th><th>负责人</th><th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="p in sorted" :key="p.id" :class="{ overdue: overdue(p) }">
          <td><code>{{ p.dev_number }}</code></td>
          <td>{{ p.plan_name }}</td>
          <td>{{ p.interval_days }}</td>
          <td>
            {{ new Date(p.next_due_at).toLocaleDateString() }}
            <small :class="{ danger: overdue(p) }">
              （{{ overdue(p) ? `已逾期 ${-daysUntil(p.next_due_at)} 天` : `还剩 ${daysUntil(p.next_due_at)} 天` }}）
            </small>
          </td>
          <td>{{ p.owner_user_name }}</td>
          <td>
            <button class="complete" @click="askComplete(p)">完成保养</button>
            <button @click="startEdit(p)">编辑</button>
            <button v-permission="['Administrators','GroupCompany','Company']" class="danger" @click="del(p)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="editing" class="drawer">
      <h3>{{ isNew ? '新增保养计划' : `编辑计划 ${editing.id}` }}</h3>
      <form @submit.prevent="save">
        <label>设备号 <input v-model="editing.dev_number" type="text" required /></label>
        <label>计划名 <input v-model="editing.plan_name" type="text" required /></label>
        <label>周期（天） <input v-model.number="editing.interval_days" type="number" min="1" required /></label>
        <label>
          下次到期
          <input
            :value="editing.next_due_at.slice(0, 10)"
            type="date"
            @input="(e: Event) => { if (editing) editing.next_due_at = new Date((e.target as HTMLInputElement).value).toISOString() }"
          />
        </label>
        <label>负责人 <input v-model="editing.owner_user_name" type="text" required /></label>
        <div class="actions">
          <button type="button" @click="editing = null">取消</button>
          <button type="submit" class="primary">保存</button>
        </div>
      </form>
    </div>

    <ConfirmDialog
      v-model="showCompleteDialog"
      title="确认完成保养"
      :message="`记录 ${completeTarget?.dev_number} · ${completeTarget?.plan_name} 本次保养？`"
      @confirm="doComplete"
    />
  </section>
</template>

<style scoped>
.maintenance { background: #fff; padding: 16px; border-radius: 6px; }
header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
h2 { font-size: 18px; }
.add { background: var(--color-primary); color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; }
.m-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.m-table th, .m-table td { padding: 8px 10px; border-bottom: 1px solid #eee; text-align: left; }
.m-table tr.overdue { background: #fff3e0; }
.m-table button { margin-right: 4px; padding: 3px 8px; border: 1px solid #ccc; background: #fff; border-radius: 3px; cursor: pointer; font-size: 12px; }
.m-table .complete { border-color: var(--color-success); color: var(--color-success); }
.m-table .danger { border-color: var(--color-error); color: var(--color-error); }
small.danger { color: var(--color-error); }
.drawer { position: fixed; right: 0; top: 0; height: 100vh; width: min(400px, 100vw); background: #fff; box-shadow: -2px 0 8px rgba(0,0,0,0.15); padding: 20px; overflow-y: auto; z-index: 500; }
.drawer form { display: flex; flex-direction: column; gap: 12px; }
.drawer label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.drawer input, .drawer select { padding: 6px 10px; border: 1px solid #ccc; border-radius: 4px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; }
.actions button { padding: 6px 14px; border: 1px solid #ccc; border-radius: 4px; cursor: pointer; }
.primary { background: var(--color-primary); color: white; border-color: var(--color-primary); }
</style>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import {
  listUsers,
  createUser,
  updateUser,
  deleteUser,
  type OrgUser,
  type OrgUserCreatePayload,
  type OrgUserUpdatePayload,
} from '@/api/orgs'
import { useAsync } from '@/composables/useAsync'
import { useToast } from '@/composables/useToast'
import LoadingSkeleton from '@/components/LoadingSkeleton.vue'
import EmptyState from '@/components/EmptyState.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import type { Authority } from '@/stores/auth'
import { useAuthStore } from '@/stores/auth'

const toast = useToast()
const auth = useAuthStore()
const loader = useAsync(listUsers)
const users = ref<OrgUser[]>([])
const query = ref('')
const authFilter = ref<'' | Authority>('')

const editing = ref<(OrgUser & { password?: string }) | null>(null)
const isNew = ref(false)
const deleteTarget = ref<OrgUser | null>(null)
const showDeleteDialog = ref(false)

async function reload(): Promise<void> {
  users.value = await loader.run()
}
onMounted(() => reload())

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  return users.value.filter((u) => {
    if (authFilter.value && u.authority !== authFilter.value) return false
    if (q && !`${u.user_name} ${u.display_name ?? ''} ${u.company ?? ''}`.toLowerCase().includes(q)) {
      return false
    }
    return true
  })
})

function startNew(): void {
  editing.value = {
    user_name: '',
    authority: 'User',
    usr_group: auth.user?.usr_group ?? '',
    control_authority: 0,
    password: '',
  }
  isNew.value = true
}

function startEdit(u: OrgUser): void {
  editing.value = { ...u }
  isNew.value = false
}

async function save(): Promise<void> {
  if (!editing.value) return
  try {
    if (isNew.value) {
      if (!editing.value.password) {
        toast.error('新建用户需设置密码')
        return
      }
      const payload: OrgUserCreatePayload = {
        user_name: editing.value.user_name,
        password: editing.value.password,
        authority: editing.value.authority,
        control_authority: editing.value.control_authority,
        group_company: editing.value.group_company,
        company: editing.value.company,
        department: editing.value.department,
      }
      await createUser(payload)
      toast.success('已创建')
    } else {
      const payload: OrgUserUpdatePayload = {
        authority: editing.value.authority,
        control_authority: editing.value.control_authority,
        group_company: editing.value.group_company,
        company: editing.value.company,
        department: editing.value.department,
      }
      await updateUser(editing.value.user_name, payload)
      toast.success('已保存')
    }
    editing.value = null
    await reload()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '保存失败')
  }
}

function askDelete(u: OrgUser): void {
  deleteTarget.value = u
  showDeleteDialog.value = true
}

async function confirmDelete(): Promise<void> {
  if (!deleteTarget.value) return
  try {
    await deleteUser(deleteTarget.value.user_name)
    toast.success('已删除用户')
    await reload()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '删除失败')
  } finally {
    deleteTarget.value = null
  }
}

function toggleControlBit(bit: number): void {
  if (!editing.value) return
  editing.value.control_authority ^= bit
}

function hasBit(n: number, bit: number): boolean {
  return (n & bit) !== 0
}
</script>

<template>
  <section class="users">
    <header>
      <h2>用户管理</h2>
      <div class="filters">
        <input v-model="query" class="search" type="text" placeholder="搜索用户名/公司…" />
        <select v-model="authFilter">
          <option value="">所有角色</option>
          <option value="Administrators">L4 超管</option>
          <option value="GroupCompany">L3 集团</option>
          <option value="Company">L2 公司</option>
          <option value="User">L1 普通</option>
        </select>
        <button v-permission="['Administrators','GroupCompany','Company']" class="add" @click="startNew">
          + 新建用户
        </button>
      </div>
    </header>

    <LoadingSkeleton v-if="loader.isPending.value" :lines="4" />
    <EmptyState v-else-if="filtered.length === 0" title="未找到用户" />
    <table v-else class="user-table">
      <thead>
        <tr>
          <th>用户名</th><th>显示名</th><th>角色</th><th>租户</th><th>公司/部门</th><th>CA</th><th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="u in filtered" :key="u.user_name">
          <td><code>{{ u.user_name }}</code></td>
          <td>{{ u.display_name ?? '—' }}</td>
          <td>{{ u.authority }}</td>
          <td>{{ u.usr_group }}</td>
          <td>{{ u.company ?? '—' }}/{{ u.department ?? '—' }}</td>
          <td>
            <span v-if="hasBit(u.control_authority, 1)" title="设备控制">⚙</span>
            <span v-if="hasBit(u.control_authority, 2)" title="配置管理">⚡</span>
            <span v-if="hasBit(u.control_authority, 4)" title="高危">🔥</span>
          </td>
          <td>
            <button @click="startEdit(u)">编辑</button>
            <button v-permission="['Administrators','GroupCompany']" class="danger" @click="askDelete(u)">
              删除
            </button>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="editing" class="drawer">
      <h3>{{ isNew ? '新建用户' : `编辑用户 ${editing.user_name}` }}</h3>
      <form @submit.prevent="save">
        <label>用户名 <input v-model="editing.user_name" type="text" :readonly="!isNew" required /></label>
        <label v-if="isNew">密码 <input v-model="editing.password" type="password" required /></label>
        <label>
          显示名
          <input :value="editing.display_name ?? '后端暂未支持'" type="text" readonly />
        </label>
        <label>
          角色
          <select v-model="editing.authority">
            <option value="User">L1 普通</option>
            <option value="Company">L2 公司</option>
            <option value="GroupCompany">L3 集团</option>
            <option value="Administrators">L4 超管</option>
          </select>
        </label>
        <label>租户 (usr_group) <input v-model="editing.usr_group" type="text" readonly /></label>
        <label>公司 <input v-model="editing.company" type="text" /></label>
        <label>部门 <input v-model="editing.department" type="text" /></label>
        <div class="ca">
          <span>控制权限（位掩码）</span>
          <label>
            <input type="checkbox" :checked="hasBit(editing.control_authority, 1)" @change="toggleControlBit(1)" />
            设备控制 (bit0)
          </label>
          <label>
            <input type="checkbox" :checked="hasBit(editing.control_authority, 2)" @change="toggleControlBit(2)" />
            配置管理 (bit1)
          </label>
          <label>
            <input type="checkbox" :checked="hasBit(editing.control_authority, 4)" @change="toggleControlBit(4)" />
            高危（触发 OTP）(bit2)
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
      :message="`确认删除用户 ${deleteTarget?.user_name}？该操作为软删除，审计记录保留 3 年。`"
      danger
      :type-to-confirm="deleteTarget?.user_name"
      @confirm="confirmDelete"
    />
  </section>
</template>

<style scoped>
.users { background: #fff; padding: 16px; border-radius: 6px; }
header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; flex-wrap: wrap; gap: 12px; }
h2 { font-size: 18px; }
.filters { display: flex; gap: 8px; flex-wrap: wrap; }
.search, select { padding: 4px 10px; border: 1px solid #ccc; border-radius: 4px; font-size: 13px; }
.add { background: var(--color-primary); color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; }
.user-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.user-table th, .user-table td { padding: 8px 10px; border-bottom: 1px solid #eee; text-align: left; }
.user-table button { margin-right: 6px; padding: 3px 8px; border: 1px solid #ccc; background: #fff; border-radius: 3px; cursor: pointer; font-size: 12px; }
.user-table .danger { border-color: var(--color-error); color: var(--color-error); }
.drawer { position: fixed; right: 0; top: 0; height: 100vh; width: min(420px, 100vw); background: #fff; box-shadow: -2px 0 8px rgba(0,0,0,0.15); padding: 20px; overflow-y: auto; z-index: 500; }
.drawer form { display: flex; flex-direction: column; gap: 12px; }
.drawer label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.drawer input, .drawer select { padding: 6px 10px; border: 1px solid #ccc; border-radius: 4px; }
.drawer input:read-only { background: #f5f5f5; }
.ca { display: flex; flex-direction: column; gap: 6px; font-size: 13px; padding: 8px; border: 1px dashed #ddd; border-radius: 4px; }
.ca label { flex-direction: row !important; align-items: center; gap: 6px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; }
.actions button { padding: 6px 14px; border: 1px solid #ccc; border-radius: 4px; cursor: pointer; }
.primary { background: var(--color-primary); color: white; border-color: var(--color-primary); }
</style>

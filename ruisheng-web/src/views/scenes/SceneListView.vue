<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { listPages, createPage, deletePage, type ScenePage } from '@/api/scenes'
import { useAsync } from '@/composables/useAsync'
import { useToast } from '@/composables/useToast'
import LoadingSkeleton from '@/components/LoadingSkeleton.vue'
import EmptyState from '@/components/EmptyState.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'

const router = useRouter()
const toast = useToast()
const loader = useAsync(listPages)
const pages = ref<ScenePage[]>([])

const showNew = ref(false)
const newName = ref('')
const deleteTarget = ref<ScenePage | null>(null)
const showDeleteDialog = ref(false)

async function reload(): Promise<void> {
  pages.value = await loader.run()
}
onMounted(() => reload())

async function createNew(): Promise<void> {
  if (!newName.value.trim()) return
  try {
    await createPage({ page_name: newName.value, pos_x: 0, pos_y: 0 })
    toast.success('已创建画面')
    showNew.value = false
    newName.value = ''
    await reload()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '创建失败')
  }
}

function askDelete(p: ScenePage): void {
  deleteTarget.value = p
  showDeleteDialog.value = true
}

async function confirmDelete(): Promise<void> {
  if (!deleteTarget.value) return
  try {
    await deletePage(deleteTarget.value.id)
    toast.success('已删除')
    await reload()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '删除失败')
  } finally {
    deleteTarget.value = null
  }
}

function open(p: ScenePage): void {
  router.push(`/scenes/${p.id}`)
}
</script>

<template>
  <section class="scenes">
    <header>
      <h2>组态画面</h2>
      <button v-permission="['Administrators','GroupCompany','Company']" class="add" @click="showNew = true">
        + 新建画面
      </button>
    </header>

    <LoadingSkeleton v-if="loader.isPending.value" :lines="3" />
    <EmptyState
      v-else-if="pages.length === 0"
      icon="🗺"
      title="尚无组态画面"
      description="创建第一张画面，将设备点位绑定到可视化画布"
      action-label="新建画面"
      @action="showNew = true"
    />
    <div v-else class="grid">
      <div v-for="p in pages" :key="p.id" class="card" @click="open(p)">
        <div class="thumb">{{ p.sonpage_pic ? '🖼' : '🗺' }}</div>
        <div class="meta">
          <div class="name">{{ p.page_name }}</div>
          <div class="sub">{{ p.owner_user_name ?? '—' }}</div>
        </div>
        <button
          v-permission="['Administrators','GroupCompany','Company']"
          class="del"
          @click.stop="askDelete(p)"
        >
          删除
        </button>
      </div>
    </div>

    <div v-if="showNew" class="modal" @click.self="showNew = false">
      <div class="modal-body">
        <h3>新建画面</h3>
        <input v-model="newName" type="text" placeholder="画面名称" />
        <div class="actions">
          <button @click="showNew = false">取消</button>
          <button class="primary" @click="createNew">创建</button>
        </div>
      </div>
    </div>

    <ConfirmDialog
      v-model="showDeleteDialog"
      :message="`确认删除画面 ${deleteTarget?.page_name}？该画面上的所有视图将一并删除。`"
      danger
      :type-to-confirm="deleteTarget?.page_name"
      @confirm="confirmDelete"
    />
  </section>
</template>

<style scoped>
.scenes { background: #fff; padding: 16px; border-radius: 6px; }
header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
h2 { font-size: 18px; }
.add { background: var(--color-primary); color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
.card { border: 1px solid #eee; border-radius: 6px; padding: 12px; cursor: pointer; display: flex; flex-direction: column; gap: 8px; position: relative; }
.card:hover { border-color: var(--color-primary); }
.thumb { height: 100px; background: #f5f5f5; display: flex; align-items: center; justify-content: center; font-size: 36px; border-radius: 4px; }
.name { font-weight: 500; font-size: 14px; }
.sub { font-size: 12px; color: var(--color-text-secondary); }
.del {
  position: absolute; right: 8px; top: 8px;
  background: #fff; border: 1px solid var(--color-error); color: var(--color-error);
  padding: 2px 8px; border-radius: 3px; cursor: pointer; font-size: 11px;
}
.modal { position: fixed; inset: 0; background: rgba(0,0,0,0.4); display: flex; align-items: center; justify-content: center; z-index: 800; }
.modal-body { background: #fff; padding: 20px; border-radius: 8px; width: min(380px, 92vw); }
.modal-body h3 { font-size: 16px; margin-bottom: 12px; }
.modal-body input { width: 100%; padding: 6px 10px; border: 1px solid #ccc; border-radius: 4px; margin-bottom: 12px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; }
.actions button { padding: 6px 14px; border: 1px solid #ccc; border-radius: 4px; cursor: pointer; }
.primary { background: var(--color-primary); color: white; border-color: var(--color-primary); }
</style>

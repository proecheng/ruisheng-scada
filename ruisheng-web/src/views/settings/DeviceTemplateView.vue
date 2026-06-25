<script setup lang="ts">
import { onMounted, ref } from 'vue'
import {
  createDeviceTemplate,
  deleteDeviceTemplate,
  listDeviceTemplates,
  updateDeviceTemplate,
  type DeviceTemplate,
} from '@/api/templates'
import type { PointConfig } from '@/api/points'
import { useToast } from '@/composables/useToast'
import LoadingSkeleton from '@/components/LoadingSkeleton.vue'

const toast = useToast()
const templates = ref<DeviceTemplate[]>([])
const isLoading = ref(false)
const editing = ref<DeviceTemplate | null>(null)
const name = ref('')
const devType = ref('')
const pointsJson = ref('[]')

async function reload(): Promise<void> {
  isLoading.value = true
  try {
    templates.value = await listDeviceTemplates()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '加载模板失败')
  } finally {
    isLoading.value = false
  }
}

onMounted(() => reload())

function startNew(): void {
  editing.value = null
  name.value = ''
  devType.value = ''
  pointsJson.value = '[]'
}

function startEdit(t: DeviceTemplate): void {
  editing.value = t
  name.value = t.name
  devType.value = t.dev_type ?? ''
  pointsJson.value = JSON.stringify(t.payload.points, null, 2)
}

function parsePoints(): PointConfig[] {
  const parsed = JSON.parse(pointsJson.value) as PointConfig[]
  if (!Array.isArray(parsed)) throw new Error('点位配置必须是数组')
  return parsed
}

async function save(): Promise<void> {
  try {
    const points = parsePoints()
    if (editing.value) {
      await updateDeviceTemplate(editing.value.id, {
        name: name.value.trim(),
        dev_type: devType.value.trim() || undefined,
        points,
      })
      toast.success('已保存模板')
    } else {
      await createDeviceTemplate({
        name: name.value.trim(),
        dev_type: devType.value.trim() || undefined,
        points,
      })
      toast.success('已创建模板')
    }
    startNew()
    await reload()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '保存模板失败')
  }
}

async function remove(t: DeviceTemplate): Promise<void> {
  try {
    await deleteDeviceTemplate(t.id)
    toast.success('已删除模板')
    if (editing.value?.id === t.id) startNew()
    await reload()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '删除模板失败')
  }
}
</script>

<template>
  <section class="templates">
    <header>
      <h2>设备模板</h2>
      <button class="add" @click="startNew">新建模板</button>
    </header>

    <LoadingSkeleton v-if="isLoading" :lines="4" />
    <div v-else class="layout">
      <table>
        <thead>
          <tr><th>名称</th><th>设备类型</th><th>点位数</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="t in templates" :key="t.id">
            <td>{{ t.name }}</td>
            <td>{{ t.dev_type ?? '—' }}</td>
            <td>{{ t.payload.points.length }}</td>
            <td>
              <button @click="startEdit(t)">编辑</button>
              <button class="danger" @click="remove(t)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>

      <form class="editor" @submit.prevent="save">
        <h3>{{ editing ? '编辑模板' : '新建模板' }}</h3>
        <label>模板名称 <input v-model="name" required /></label>
        <label>设备类型 <input v-model="devType" placeholder="pump" /></label>
        <label>
          点位配置 JSON
          <textarea v-model="pointsJson" rows="16" spellcheck="false" />
        </label>
        <button type="submit" class="primary">保存模板</button>
      </form>
    </div>
  </section>
</template>

<style scoped>
.templates { background: #fff; padding: 16px; border-radius: 6px; }
header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
h2 { font-size: 18px; }
.add, .primary { background: var(--color-primary); color: white; border: none; padding: 7px 14px; border-radius: 4px; cursor: pointer; }
.layout { display: grid; grid-template-columns: minmax(420px, 1fr) 420px; gap: 16px; align-items: start; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #eee; text-align: left; }
td button { margin-right: 6px; padding: 3px 8px; border: 1px solid #ccc; background: #fff; border-radius: 3px; cursor: pointer; font-size: 12px; }
.danger { border-color: var(--color-error); color: var(--color-error); }
.editor { display: flex; flex-direction: column; gap: 10px; border: 1px solid #e5e7eb; border-radius: 6px; padding: 14px; }
.editor h3 { font-size: 16px; }
.editor label { display: flex; flex-direction: column; gap: 5px; font-size: 12px; color: var(--color-text-secondary); }
.editor input, .editor textarea { padding: 7px 9px; border: 1px solid #ccc; border-radius: 4px; font-size: 13px; }
.editor textarea { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; resize: vertical; }
@media (max-width: 980px) { .layout { grid-template-columns: 1fr; } }
</style>

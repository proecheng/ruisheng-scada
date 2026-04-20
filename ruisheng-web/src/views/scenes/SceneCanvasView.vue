<script setup lang="ts">
import { ref, onMounted, watch, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  listViews,
  createView,
  deleteView,
  type SceneView,
} from '@/api/scenes'
import { useAsync } from '@/composables/useAsync'
import { useToast } from '@/composables/useToast'
import { useWsStore } from '@/stores/ws'
import LoadingSkeleton from '@/components/LoadingSkeleton.vue'

const props = defineProps<{ pageId: string }>()
const router = useRouter()
const toast = useToast()
const wsStore = useWsStore()

const stageConfig = ref({ width: 900, height: 600 })
const views = ref<SceneView[]>([])
const pointValues = ref<Record<string, number>>({})
const editMode = ref(false)
const selectedView = ref<SceneView | null>(null)

const loader = useAsync(() => listViews(Number(props.pageId)))

async function reload(): Promise<void> {
  views.value = await loader.run()
}

onMounted(() => {
  stageConfig.value = { width: window.innerWidth - 260, height: window.innerHeight - 140 }
  void reload()
  window.addEventListener('resize', onResize)
})

onUnmounted(() => window.removeEventListener('resize', onResize))

function onResize(): void {
  stageConfig.value = { width: window.innerWidth - 260, height: window.innerHeight - 140 }
}

watch(
  () => wsStore.lastMessage,
  (m) => {
    if (!m || m.type !== 'realtime') return
    pointValues.value[`${m.dev_number}:${m.point_id}`] = m.value
  },
)

function viewLabel(v: SceneView): string {
  const binding = v.point_bindings?.[0]
  if (!binding) return v.dev_number
  const key = `${v.dev_number}:${binding.point_id}`
  const val = pointValues.value[key]
  return val !== undefined ? `${binding.label}: ${val}` : `${binding.label}: —`
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
async function onStageClick(e: any): Promise<void> {
  if (!editMode.value) return
  const pos = e.currentTarget.getPointerPosition()
  if (!pos) return
  const devNumber = prompt('设备号:') ?? ''
  if (!devNumber) return
  try {
    const v = await createView(Number(props.pageId), {
      dev_number: devNumber,
      view_type: 'default',
      shape: 'circle',
      x: pos.x,
      y: pos.y,
      width: 40,
      height: 40,
    })
    views.value.push(v)
    toast.success('已添加视图')
  } catch (err) {
    toast.error(err instanceof Error ? err.message : '添加失败')
  }
}

async function removeSelected(): Promise<void> {
  if (!selectedView.value) return
  const v = selectedView.value
  try {
    await deleteView(Number(props.pageId), v.id)
    views.value = views.value.filter((x) => x.id !== v.id)
    selectedView.value = null
    toast.success('已删除')
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '删除失败')
  }
}

function selectView(v: SceneView): void {
  selectedView.value = v
  if (!editMode.value) {
    void router.push(`/devices/${v.dev_number}`)
  }
}
</script>

<template>
  <section class="canvas-view">
    <header>
      <button class="back" @click="router.back()">← 返回画面列表</button>
      <h2>组态画面 #{{ pageId }}</h2>
      <div class="spacer"></div>
      <label v-permission="['Administrators','GroupCompany','Company']" class="edit-toggle">
        <input v-model="editMode" type="checkbox" /> 编辑模式
      </label>
      <button
        v-if="editMode && selectedView"
        class="danger"
        @click="removeSelected"
      >
        删除所选
      </button>
    </header>

    <LoadingSkeleton v-if="loader.isPending.value" :lines="4" />
    <p v-if="editMode" class="hint">编辑模式：点击画布空白处添加设备视图；选中后可删除</p>

    <v-stage
      v-if="!loader.isPending.value"
      :config="stageConfig"
      class="stage"
      @click="onStageClick"
    >
      <v-layer>
        <template v-for="v in views" :key="v.id">
          <v-circle
            v-if="v.shape === 'circle'"
            :config="{
              x: v.x, y: v.y, radius: (v.width ?? 40) / 2,
              fill: selectedView?.id === v.id ? '#ffca28' : '#42a5f5',
              stroke: '#1565c0', strokeWidth: 2,
            }"
            @click="selectView(v)"
          />
          <v-rect
            v-else-if="v.shape === 'rect'"
            :config="{
              x: v.x, y: v.y, width: v.width, height: v.height,
              fill: selectedView?.id === v.id ? '#ffca28' : '#66bb6a',
              stroke: '#2e7d32', strokeWidth: 2,
            }"
            @click="selectView(v)"
          />
          <v-text
            :config="{
              x: v.x - 40, y: v.y + (v.height ?? 40) / 2 + 6,
              text: viewLabel(v),
              fontSize: 12, fill: '#212121', width: 120, align: 'center',
            }"
          />
        </template>
      </v-layer>
    </v-stage>
  </section>
</template>

<style scoped>
.canvas-view { background: #fff; padding: 16px; border-radius: 6px; }
header { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }
.back { background: none; border: 1px solid #ccc; padding: 4px 10px; border-radius: 4px; cursor: pointer; }
h2 { font-size: 18px; }
.spacer { flex: 1; }
.edit-toggle { display: flex; align-items: center; gap: 6px; font-size: 13px; }
.danger { background: #fff; border: 1px solid var(--color-error); color: var(--color-error); padding: 4px 12px; border-radius: 4px; cursor: pointer; }
.hint { font-size: 12px; color: var(--color-text-secondary); margin-bottom: 8px; }
.stage { border: 1px solid #eee; background: #fafafa; }
</style>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useDevicesStore, type DeviceSummary } from '@/stores/devices'

const props = defineProps<{ onSelect?: (dev: DeviceSummary) => void }>()
const store = useDevicesStore()
const query = ref('')

interface TreeNode {
  key: string
  label: string
  children?: TreeNode[]
  device?: DeviceSummary
}

const tree = computed<TreeNode[]>(() => {
  const q = query.value.trim().toLowerCase()
  const devices = store.list.filter(
    (d) => !q || `${d.dev_number} ${d.dev_name}`.toLowerCase().includes(q),
  )
  const byCompany = new Map<string, Map<string, DeviceSummary[]>>()
  for (const d of devices) {
    const c = d.company ?? '未分类'
    const dept = d.department ?? '默认'
    if (!byCompany.has(c)) byCompany.set(c, new Map())
    const dm = byCompany.get(c)!
    if (!dm.has(dept)) dm.set(dept, [])
    dm.get(dept)!.push(d)
  }
  const nodes: TreeNode[] = []
  for (const [c, depts] of byCompany) {
    const cn: TreeNode = { key: `c:${c}`, label: c, children: [] }
    for (const [dept, devs] of depts) {
      const dn: TreeNode = {
        key: `c:${c}/d:${dept}`,
        label: `${dept} (${devs.length})`,
        children: devs.map((d) => ({
          key: `dev:${d.dev_number}`,
          label: `${d.dev_number} · ${d.dev_name}`,
          device: d,
        })),
      }
      cn.children!.push(dn)
    }
    nodes.push(cn)
  }
  return nodes
})

const expanded = ref<Set<string>>(new Set())
function toggle(key: string): void {
  if (expanded.value.has(key)) expanded.value.delete(key)
  else expanded.value.add(key)
  // Vue reactivity for Set requires creating a new Set
  expanded.value = new Set(expanded.value)
}

function onClickNode(n: TreeNode): void {
  if (n.device) {
    store.select(n.device.dev_number)
    props.onSelect?.(n.device)
  } else {
    toggle(n.key)
  }
}
</script>

<template>
  <div class="device-tree">
    <input v-model="query" class="tree-search" placeholder="搜索设备…" />
    <ul class="tree-root">
      <template v-for="n in tree" :key="n.key">
        <li>
          <div class="node" @click="onClickNode(n)">
            <span class="caret">{{ expanded.has(n.key) ? '▾' : '▸' }}</span>
            {{ n.label }}
          </div>
          <ul v-if="n.children && expanded.has(n.key)" class="tree-sub">
            <template v-for="sub in n.children" :key="sub.key">
              <li>
                <div class="node" @click="onClickNode(sub)">
                  <span class="caret">{{ expanded.has(sub.key) ? '▾' : '▸' }}</span>
                  {{ sub.label }}
                </div>
                <ul v-if="sub.children && expanded.has(sub.key)" class="tree-sub">
                  <li
                    v-for="leaf in sub.children"
                    :key="leaf.key"
                    class="leaf"
                    :class="{ selected: store.selectedDevNumber === leaf.device?.dev_number }"
                    @click="onClickNode(leaf)"
                  >
                    <span class="dot" :data-state="leaf.device?.state"></span>
                    {{ leaf.label }}
                  </li>
                </ul>
              </li>
            </template>
          </ul>
        </li>
      </template>
    </ul>
  </div>
</template>

<style scoped>
.device-tree { font-size: 13px; }
.tree-search { width: 100%; padding: 6px; border: 1px solid #ccc; border-radius: 4px; margin-bottom: 8px; }
ul { list-style: none; padding-left: 0; }
.tree-sub { padding-left: 14px; }
.node { padding: 4px 6px; cursor: pointer; display: flex; gap: 4px; border-radius: 3px; }
.node:hover { background: #f0f0f0; }
.caret { width: 10px; text-align: center; color: var(--color-text-secondary); }
.leaf.selected { background: #e3f2fd; font-weight: 500; }
.dot { width: 6px; height: 6px; border-radius: 50%; display: inline-block; margin-right: 4px; }
.dot[data-state='online'] { background: var(--color-success); }
.dot[data-state='offline'] { background: #bbb; }
.dot[data-state='warning'] { background: var(--color-warning); }
</style>

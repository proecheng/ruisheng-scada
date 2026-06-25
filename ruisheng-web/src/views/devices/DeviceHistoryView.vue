<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getHistory, type HistoryPage } from '@/api/devices'
import { listPoints, type PointConfig } from '@/api/points'
import { useAsync } from '@/composables/useAsync'
import { useToast } from '@/composables/useToast'
import LoadingSkeleton from '@/components/LoadingSkeleton.vue'

type ECharts = import('echarts').ECharts
type EChartsModule = typeof import('echarts')

const props = defineProps<{ devNumber: string }>()
const route = useRoute()
const router = useRouter()
const toast = useToast()

const points = ref<PointConfig[]>([])
const selectedPointIds = ref<number[]>([])
const fromDate = ref<string>(new Date(Date.now() - 24 * 3600000).toISOString().slice(0, 16))
const toDate = ref<string>(new Date().toISOString().slice(0, 16))
const viewMode = ref<'combined' | 'chart' | 'table'>('combined')
const historyPage = ref<HistoryPage | null>(null)

const chartRef = ref<HTMLDivElement | null>(null)
let chart: ECharts | null = null
let echartsModule: EChartsModule | null = null

const pointsLoader = useAsync(() => listPoints(props.devNumber))
const loader = useAsync(() =>
  getHistory(props.devNumber, {
    point_ids: selectedPointIds.value,
    from: new Date(fromDate.value).toISOString(),
    to: new Date(toDate.value).toISOString(),
  }),
)

const pointNameById = computed(() => {
  const map = new Map<number, string>()
  for (const p of points.value) {
    const register = `FC${p.fun_code} 地址 ${p.register_address}`
    const unit = p.unit ? ` / ${p.unit}` : ''
    map.set(p.point_id, `${p.point_name}（${register}${unit}）`)
  }
  return map
})

const tableRows = computed(() =>
  (historyPage.value?.points ?? []).map((p) => ({
    ts: p.ts,
    point_id: p.point_id ?? selectedPointIds.value[0],
    point_name:
      pointNameById.value.get(p.point_id ?? selectedPointIds.value[0] ?? 0) ??
      `点位 ${p.point_id ?? selectedPointIds.value[0] ?? ''}`,
    value: p.value,
  })),
)

const shouldShowChart = computed(() => viewMode.value === 'combined' || viewMode.value === 'chart')
const shouldShowTable = computed(() => viewMode.value === 'combined' || viewMode.value === 'table')

async function getEcharts(): Promise<EChartsModule> {
  echartsModule ??= await import('echarts')
  return echartsModule
}

function resize(): void {
  chart?.resize()
}

async function load(): Promise<void> {
  if (selectedPointIds.value.length === 0) {
    toast.error('请选择至少一个点位')
    return
  }
  try {
    const page = await loader.run()
    historyPage.value = page
    await nextTick()
    await renderChart()
    if (page.downsampled) {
      toast.info(`数据已降采样至 ${page.sample_interval_s}s 粒度`, { timeoutMs: 3000 })
    }
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '加载失败')
  }
}

async function renderChart(): Promise<void> {
  if (!chartRef.value || !shouldShowChart.value) return
  if (chart && chart.getDom() !== chartRef.value) {
    chart.dispose()
    chart = null
  }
  if (!chart) {
    const echarts = await getEcharts()
    chart = echarts.init(chartRef.value)
    window.addEventListener('resize', resize)
  }
  const rows = historyPage.value?.points ?? []
  chart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { top: 0 },
    xAxis: { type: 'time' },
    yAxis: { type: 'value' },
    grid: { left: 50, right: 20, top: 44, bottom: 40 },
    dataZoom: [{ type: 'inside' }, { type: 'slider' }],
    series: selectedPointIds.value.map((pointId) => ({
      type: 'line',
      name: points.value.find((p) => p.point_id === pointId)?.point_name ?? `点位 ${pointId}`,
      data: rows
        .filter((p) => (p.point_id ?? selectedPointIds.value[0]) === pointId)
        .map((p) => [p.ts, p.value]),
      showSymbol: false,
      smooth: true,
    })),
  })
}

function downloadCsv(): void {
  const header = 'timestamp,point_id,point_name,value\n'
  const body = tableRows.value
    .map((p) => `${p.ts},${p.point_id},${p.point_name},${p.value}`)
    .join('\n')
  const blob = new Blob([header + body], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${props.devNumber}-history-${Date.now()}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

onMounted(async () => {
  try {
    points.value = await pointsLoader.run()
    const initial = Number(route.query.point_id ?? 0)
    selectedPointIds.value = initial > 0 ? [initial] : points.value.slice(0, 3).map((p) => p.point_id)
    await load()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '加载点位失败')
  }
})

onUnmounted(() => {
  chart?.dispose()
  window.removeEventListener('resize', resize)
})

watch(viewMode, () => {
  void nextTick().then(renderChart)
})
</script>

<template>
  <section class="device-history">
    <header>
      <button class="back" @click="router.back()">← 返回</button>
      <h2>{{ devNumber }} — 历史数据</h2>
    </header>
    <form class="filters" @submit.prevent="load">
      <label>
        点位变量
        <select v-model="selectedPointIds" multiple size="4">
          <option v-for="p in points" :key="p.point_id" :value="p.point_id">
            {{ p.point_name }}（ID {{ p.point_id }} / FC{{ p.fun_code }} 地址 {{ p.register_address }}）
          </option>
        </select>
      </label>
      <label>起 <input v-model="fromDate" type="datetime-local" /></label>
      <label>止 <input v-model="toDate" type="datetime-local" /></label>
      <div class="mode">
        <button type="button" :class="{ active: viewMode === 'combined' }" @click="viewMode = 'combined'">图表+表格</button>
        <button type="button" :class="{ active: viewMode === 'chart' }" @click="viewMode = 'chart'">图表</button>
        <button type="button" :class="{ active: viewMode === 'table' }" @click="viewMode = 'table'">表格</button>
      </div>
      <button type="submit" :disabled="loader.isPending.value">
        {{ loader.isPending.value ? '加载中...' : '查询' }}
      </button>
      <button type="button" class="secondary" :disabled="tableRows.length === 0" @click="downloadCsv">导出结果</button>
    </form>
    <LoadingSkeleton v-if="loader.isPending.value || pointsLoader.isPending.value" :lines="4" />
    <div v-else class="history-results" :data-mode="viewMode">
      <div v-if="shouldShowChart" ref="chartRef" class="chart" />
      <table v-if="shouldShowTable" class="history-table">
        <thead>
          <tr><th>时间</th><th>变量</th><th>值</th></tr>
        </thead>
        <tbody>
          <tr v-for="row in tableRows" :key="`${row.ts}-${row.point_id}`">
            <td>{{ new Date(row.ts).toLocaleString() }}</td>
            <td>{{ row.point_name }}</td>
            <td>{{ row.value }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.device-history { background: #fff; padding: 16px; border-radius: 6px; }
.back { background: none; border: 1px solid #ccc; padding: 4px 10px; border-radius: 4px; cursor: pointer; margin-right: 8px; }
header { margin-bottom: 12px; }
h2 { display: inline; font-size: 18px; }
.filters { display: flex; gap: 10px; align-items: end; margin-bottom: 16px; flex-wrap: wrap; }
.filters label { display: flex; flex-direction: column; font-size: 12px; gap: 4px; }
.filters input, .filters select { padding: 4px 8px; border: 1px solid #ccc; border-radius: 4px; min-width: 180px; }
.filters select[multiple] { min-width: 320px; }
.filters button { background: var(--color-primary); color: white; border: none; padding: 6px 14px; border-radius: 4px; cursor: pointer; }
.filters button:disabled { opacity: 0.55; cursor: not-allowed; }
.filters .secondary { background: #fff; color: var(--color-text); border: 1px solid #ccc; }
.mode { display: flex; border: 1px solid #ccc; border-radius: 4px; overflow: hidden; }
.mode button { border-radius: 0; background: #fff; color: var(--color-text); }
.mode button.active { background: var(--color-primary); color: white; }
.history-results { display: grid; gap: 16px; }
.chart { height: 440px; }
.history-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.history-table th, .history-table td { padding: 8px 10px; border-bottom: 1px solid #eee; text-align: left; }
</style>

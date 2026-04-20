<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import * as echarts from 'echarts'
import { getHistory } from '@/api/devices'
import { useAsync } from '@/composables/useAsync'
import { useToast } from '@/composables/useToast'
import LoadingSkeleton from '@/components/LoadingSkeleton.vue'

const props = defineProps<{ devNumber: string }>()
const route = useRoute()
const router = useRouter()
const toast = useToast()

const pointId = ref<number>(Number(route.query.point_id ?? 1))
const fromDate = ref<string>(new Date(Date.now() - 24 * 3600000).toISOString().slice(0, 16))
const toDate = ref<string>(new Date().toISOString().slice(0, 16))

const chartRef = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null

const loader = useAsync(() =>
  getHistory(props.devNumber, {
    point_id: pointId.value,
    from: new Date(fromDate.value).toISOString(),
    to: new Date(toDate.value).toISOString(),
  }),
)

async function load(): Promise<void> {
  try {
    const page = await loader.run()
    await nextTick()
    if (!chart && chartRef.value) {
      chart = echarts.init(chartRef.value)
      window.addEventListener('resize', resize)
    }
    chart?.setOption({
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'time' },
      yAxis: { type: 'value' },
      grid: { left: 50, right: 20, top: 20, bottom: 40 },
      toolbox: {
        right: 10,
        feature: {
          dataZoom: {},
          restore: {},
          saveAsImage: { title: '保存图片' },
          myDownload: {
            show: true,
            title: '下载 CSV',
            icon: 'path://M128 128h768v768H128z',
            onclick: () => downloadCsv(page.points),
          },
        },
      },
      dataZoom: [{ type: 'inside' }, { type: 'slider' }],
      series: [
        {
          type: 'line',
          name: `点位 ${pointId.value}`,
          data: page.points.map((p) => [p.ts, p.value]),
          showSymbol: false,
          smooth: true,
        },
      ],
    })
    if (page.downsampled) {
      toast.info(`数据已降采样至 ${page.sample_interval_s}s 粒度`, { timeoutMs: 3000 })
    }
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '加载失败')
  }
}

function resize(): void { chart?.resize() }

function downloadCsv(points: Array<{ ts: string; value: number }>): void {
  const header = 'timestamp,value\n'
  const body = points.map((p) => `${p.ts},${p.value}`).join('\n')
  const blob = new Blob([header + body], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${props.devNumber}-point${pointId.value}-${Date.now()}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

onMounted(() => load())
onUnmounted(() => {
  chart?.dispose()
  window.removeEventListener('resize', resize)
})

watch(() => route.query.point_id, (v) => {
  pointId.value = Number(v ?? 1)
  void load()
})
</script>

<template>
  <section class="device-history">
    <header>
      <button class="back" @click="router.back()">← 返回</button>
      <h2>{{ devNumber }} — 历史数据</h2>
    </header>
    <form class="filters" @submit.prevent="load">
      <label>点位 ID <input v-model.number="pointId" type="number" /></label>
      <label>起 <input v-model="fromDate" type="datetime-local" /></label>
      <label>止 <input v-model="toDate" type="datetime-local" /></label>
      <button type="submit" :disabled="loader.isPending.value">
        {{ loader.isPending.value ? '加载中…' : '查询' }}
      </button>
    </form>
    <LoadingSkeleton v-if="loader.isPending.value" :lines="4" />
    <div v-else ref="chartRef" class="chart" />
  </section>
</template>

<style scoped>
.device-history { background: #fff; padding: 16px; border-radius: 6px; }
.back { background: none; border: 1px solid #ccc; padding: 4px 10px; border-radius: 4px; cursor: pointer; margin-right: 8px; }
header { margin-bottom: 12px; }
h2 { display: inline; font-size: 18px; }
.filters { display: flex; gap: 10px; align-items: end; margin-bottom: 16px; flex-wrap: wrap; }
.filters label { display: flex; flex-direction: column; font-size: 12px; gap: 4px; }
.filters input { padding: 4px 8px; border: 1px solid #ccc; border-radius: 4px; }
.filters button { background: var(--color-primary); color: white; border: none; padding: 6px 14px; border-radius: 4px; cursor: pointer; }
.chart { height: 420px; }
</style>

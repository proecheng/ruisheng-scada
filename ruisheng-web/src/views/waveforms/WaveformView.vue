<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import * as echarts from 'echarts'
import {
  getLatestWaveform,
  analyzeWaveform,
  type WaveformSample,
  type AnalysisResult,
  type AnalysisType,
} from '@/api/waveforms'
import { useAsync } from '@/composables/useAsync'
import { useToast } from '@/composables/useToast'
import LoadingSkeleton from '@/components/LoadingSkeleton.vue'

const toast = useToast()

const devNumber = ref('')
const pointId = ref<number>(1)
const analysisType = ref<AnalysisType>('FFT')

const wave = ref<WaveformSample | null>(null)
const analysis = ref<AnalysisResult | null>(null)

const waveChartRef = ref<HTMLDivElement | null>(null)
const specChartRef = ref<HTMLDivElement | null>(null)
let waveChart: echarts.ECharts | null = null
let specChart: echarts.ECharts | null = null

const loader = useAsync(async () => {
  const w = await getLatestWaveform(devNumber.value, pointId.value)
  wave.value = w
  const a = await analyzeWaveform(devNumber.value, pointId.value, analysisType.value)
  analysis.value = a
  await nextTick()
  renderCharts()
  return { wave: w, analysis: a }
})

function renderCharts(): void {
  if (wave.value && waveChartRef.value) {
    if (!waveChart) waveChart = echarts.init(waveChartRef.value)
    waveChart.setOption({
      title: { text: '时域波形', textStyle: { fontSize: 13 } },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: wave.value.samples.map((_, i) => i) },
      yAxis: { type: 'value' },
      grid: { left: 50, right: 20, top: 30, bottom: 30 },
      series: [{ type: 'line', data: wave.value.samples, showSymbol: false }],
      toolbox: { right: 10, feature: { saveAsImage: {} } },
    })
  }
  if (analysis.value && specChartRef.value) {
    if (!specChart) specChart = echarts.init(specChartRef.value)
    specChart.setOption({
      title: { text: `${analysis.value.type} 谱`, textStyle: { fontSize: 13 } },
      tooltip: { trigger: 'axis' },
      xAxis: {
        type: 'category',
        name: 'Hz',
        data: analysis.value.peaks.map((p) => p.freq_hz.toFixed(1)),
      },
      yAxis: { type: 'value', name: '幅值' },
      grid: { left: 50, right: 20, top: 30, bottom: 40 },
      series: [{ type: 'bar', data: analysis.value.peaks.map((p) => p.amplitude) }],
      toolbox: { right: 10, feature: { saveAsImage: {} } },
    })
  }
}

function resize(): void {
  waveChart?.resize()
  specChart?.resize()
}

onMounted(() => window.addEventListener('resize', resize))
onUnmounted(() => {
  waveChart?.dispose()
  specChart?.dispose()
  window.removeEventListener('resize', resize)
})

async function submit(): Promise<void> {
  if (!devNumber.value) {
    toast.error('请输入设备号')
    return
  }
  try {
    await loader.run()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '加载失败')
  }
}
</script>

<template>
  <section class="waveform">
    <header>
      <h2>波形分析</h2>
      <form class="bar" @submit.prevent="submit">
        <label>设备号 <input v-model="devNumber" type="text" required /></label>
        <label>点位 <input v-model.number="pointId" type="number" required /></label>
        <label>
          分析
          <select v-model="analysisType">
            <option value="FFT">FFT</option>
            <option value="OPM">OPM</option>
          </select>
        </label>
        <button type="submit" :disabled="loader.isPending.value">
          {{ loader.isPending.value ? '分析中…' : '分析' }}
        </button>
      </form>
    </header>

    <LoadingSkeleton v-if="loader.isPending.value" :lines="6" />
    <div v-else class="charts">
      <div ref="waveChartRef" class="chart" />
      <div ref="specChartRef" class="chart" />
    </div>
  </section>
</template>

<style scoped>
.waveform { background: #fff; padding: 16px; border-radius: 6px; }
header { display: flex; justify-content: space-between; align-items: start; margin-bottom: 16px; flex-wrap: wrap; gap: 12px; }
h2 { font-size: 18px; }
.bar { display: flex; gap: 10px; align-items: end; }
.bar label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; }
.bar input, .bar select { padding: 4px 8px; border: 1px solid #ccc; border-radius: 4px; }
.bar button { padding: 6px 14px; background: var(--color-primary); color: white; border: none; border-radius: 4px; cursor: pointer; }
.charts { display: grid; grid-template-columns: 1fr; gap: 12px; }
.chart { height: 320px; border: 1px solid #eee; border-radius: 4px; }
@media (min-width: 1024px) { .charts { grid-template-columns: 1fr 1fr; } }
</style>

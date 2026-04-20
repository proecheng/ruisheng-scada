<script setup lang="ts">
import { ref } from 'vue'
import {
  generateDailyReport,
  downloadDailyReportXlsx,
  type DailyReportRow,
} from '@/api/reports'
import { useAsync } from '@/composables/useAsync'
import { useToast } from '@/composables/useToast'
import LoadingSkeleton from '@/components/LoadingSkeleton.vue'
import EmptyState from '@/components/EmptyState.vue'

const toast = useToast()

const date = ref<string>(new Date().toISOString().slice(0, 10))
const rows = ref<DailyReportRow[]>([])

const loader = useAsync(() => generateDailyReport({ date: date.value }))

async function run(): Promise<void> {
  rows.value = await loader.run()
}

async function exportXlsx(): Promise<void> {
  try {
    const blob = await downloadDailyReportXlsx({ date: date.value })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `daily-${date.value}.xlsx`
    a.click()
    URL.revokeObjectURL(url)
    toast.success('已下载')
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '导出失败')
  }
}
</script>

<template>
  <section class="report">
    <header>
      <h2>日报表</h2>
      <div class="bar">
        <label>日期 <input v-model="date" type="date" /></label>
        <button :disabled="loader.isPending.value" @click="run">
          {{ loader.isPending.value ? '生成中…' : '生成' }}
        </button>
        <button v-if="rows.length" class="secondary" @click="exportXlsx">导出 Excel</button>
      </div>
    </header>

    <LoadingSkeleton v-if="loader.isPending.value" :lines="4" />
    <EmptyState v-else-if="rows.length === 0" title="未生成数据" description="选择日期后点击生成" />
    <table v-else class="r-table">
      <thead>
        <tr>
          <th>设备号</th><th>名称</th><th>采集点位</th><th>告警数</th><th>停机 (min)</th><th>产量</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in rows" :key="r.dev_number">
          <td><code>{{ r.dev_number }}</code></td>
          <td>{{ r.dev_name }}</td>
          <td>{{ r.total_points }}</td>
          <td :class="{ warning: r.alarm_count > 0 }">{{ r.alarm_count }}</td>
          <td>{{ r.downtime_min }}</td>
          <td>{{ r.production ?? '—' }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<style scoped>
.report { background: #fff; padding: 16px; border-radius: 6px; }
header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; flex-wrap: wrap; gap: 12px; }
h2 { font-size: 18px; }
.bar { display: flex; gap: 8px; align-items: end; }
.bar label { display: flex; flex-direction: column; font-size: 12px; gap: 4px; }
.bar input { padding: 4px 8px; border: 1px solid #ccc; border-radius: 4px; }
.bar button { padding: 6px 14px; background: var(--color-primary); color: white; border: none; border-radius: 4px; cursor: pointer; }
.bar button:disabled { opacity: 0.5; }
.bar button.secondary { background: #fff; color: var(--color-text); border: 1px solid #ccc; }
.r-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.r-table th, .r-table td { padding: 8px 10px; border-bottom: 1px solid #eee; text-align: left; }
.warning { color: var(--color-warning); font-weight: 500; }
</style>

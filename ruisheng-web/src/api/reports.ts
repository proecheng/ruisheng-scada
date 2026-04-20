import { apiClient } from '@/api/client'

export interface DailyReportRequest {
  date: string
  dev_numbers?: string[]
  format?: 'json' | 'xlsx'
}

export interface DailyReportRow {
  dev_number: string
  dev_name: string
  total_points: number
  alarm_count: number
  downtime_min: number
  production?: number
}

export async function generateDailyReport(req: DailyReportRequest): Promise<DailyReportRow[]> {
  const { data } = await apiClient.post('/reports/daily', req)
  return data.data as DailyReportRow[]
}

export async function downloadDailyReportXlsx(req: DailyReportRequest): Promise<Blob> {
  const res = await apiClient.post(
    '/reports/daily',
    { ...req, format: 'xlsx' },
    { responseType: 'blob' },
  )
  return res.data as Blob
}

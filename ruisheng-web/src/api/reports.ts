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

type BackendDailyReport = Record<string, Record<string, { count: number; avg?: number | null }>>

function toRows(payload: DailyReportRow[] | BackendDailyReport): DailyReportRow[] {
  if (Array.isArray(payload)) return payload
  return Object.entries(payload).map(([devNumber, points]) => {
    const stats = Object.values(points)
    const production = stats.reduce((sum, p) => sum + Number(p.avg ?? 0), 0)
    return {
      dev_number: devNumber,
      dev_name: devNumber,
      total_points: stats.length,
      alarm_count: 0,
      downtime_min: 0,
      production,
    }
  })
}

function toBackendRequest(req: DailyReportRequest): {
  day: string
  dev_number?: string
  format?: 'json' | 'xlsx'
} {
  return {
    day: req.date,
    dev_number: req.dev_numbers?.[0],
    format: req.format,
  }
}

export async function generateDailyReport(req: DailyReportRequest): Promise<DailyReportRow[]> {
  const { data } = await apiClient.post('/reports/daily', toBackendRequest(req))
  return toRows(data.data as DailyReportRow[] | BackendDailyReport)
}

export async function downloadDailyReportXlsx(req: DailyReportRequest): Promise<Blob> {
  const res = await apiClient.post(
    '/reports/daily',
    { ...toBackendRequest(req), format: 'xlsx' },
    { responseType: 'blob' },
  )
  return res.data as Blob
}

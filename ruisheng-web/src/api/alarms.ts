import { apiClient } from '@/api/client'

export type AlarmType = '>' | '<' | '=' | '!=' | 'LX'

export interface AlarmConfig {
  cfg_id: number
  point_id: number
  alarm_name: string
  alarm_type: AlarmType
  limit: number
  relation_point_id?: number
  severity: 'info' | 'warning' | 'critical'
  channels: string[]
}

export interface AlarmRecord {
  event_id: number
  dev_number: string
  cfg_id: number
  alarm_name: string
  value: number
  limit: number
  severity: string
  ts: string
  acked_at?: string
  acked_by?: string
}

export async function listAlarmConfigs(devNumber: string): Promise<AlarmConfig[]> {
  const { data } = await apiClient.get(`/devices/${devNumber}/alarms/configs`)
  return data.data as AlarmConfig[]
}

export async function createAlarmConfig(
  devNumber: string,
  p: Omit<AlarmConfig, 'cfg_id'>,
): Promise<AlarmConfig> {
  const { data } = await apiClient.post(`/devices/${devNumber}/alarms/configs`, p)
  return data.data as AlarmConfig
}

export async function updateAlarmConfig(
  devNumber: string,
  cfgId: number,
  p: Partial<AlarmConfig>,
): Promise<AlarmConfig> {
  const { data } = await apiClient.put(`/devices/${devNumber}/alarms/configs/${cfgId}`, p)
  return data.data as AlarmConfig
}

export async function deleteAlarmConfig(devNumber: string, cfgId: number): Promise<void> {
  await apiClient.delete(`/devices/${devNumber}/alarms/configs/${cfgId}`)
}

export async function listAlarms(params?: {
  dev_number?: string
  from?: string
  to?: string
  severity?: string
  acked?: boolean
  cursor?: string
}): Promise<{ items: AlarmRecord[]; next_cursor: string | null }> {
  const { data } = await apiClient.get('/alarms', { params })
  return data.data as { items: AlarmRecord[]; next_cursor: string | null }
}

export async function resetAlarm(alarmId: number): Promise<void> {
  await apiClient.put(`/alarms/${alarmId}/reset`, {})
}

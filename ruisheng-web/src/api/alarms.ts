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

interface ListEnvelope<T> {
  items: T[]
  next_cursor?: string | null
}

interface AlarmConfigWire {
  id: number
  cfg_id?: number
  point_id: number
  alarm_name: string
  alarm_type: AlarmType
  limit_value?: number
  limit?: number
  relation_point_id?: number
  severity?: 'info' | 'warning' | 'critical'
  phone_alarm?: number
  channels?: string[]
}

interface AlarmRecordWire {
  id?: number
  event_id?: number
  dev_number: string
  cfg_id?: number
  point_id?: number
  alarm_name?: string
  alarm_msg?: string
  alarm_value?: number
  value?: number
  limit?: number
  severity?: string
  triggered_at?: string
  ts?: string
  reset_at?: string | null
  acked_at?: string
  acked_by?: string
}

function itemsOf<T>(payload: T[] | ListEnvelope<T> | undefined): T[] {
  if (Array.isArray(payload)) return payload
  return payload?.items ?? []
}

function toAlarmConfig(c: AlarmConfigWire): AlarmConfig {
  return {
    cfg_id: c.cfg_id ?? c.id,
    point_id: c.point_id,
    alarm_name: c.alarm_name,
    alarm_type: c.alarm_type,
    limit: Number(c.limit ?? c.limit_value ?? 0),
    relation_point_id: c.relation_point_id,
    severity: c.severity ?? 'warning',
    channels: c.channels ?? (c.phone_alarm ? ['wechat', 'sms'] : []),
  }
}

function toAlarmConfigCreatePayload(c: Partial<AlarmConfig>) {
  return {
    point_id: c.point_id,
    alarm_name: c.alarm_name,
    alarm_type: c.alarm_type,
    limit_value: c.limit,
    relation_point_id: c.relation_point_id,
    enable: true,
    phone_alarm: c.channels?.length ?? 0,
  }
}

function toAlarmConfigUpdatePayload(c: Partial<AlarmConfig>) {
  return {
    alarm_name: c.alarm_name,
    alarm_type: c.alarm_type,
    limit_value: c.limit,
    relation_point_id: c.relation_point_id,
    phone_alarm: c.channels?.length,
  }
}

function toAlarmRecord(a: AlarmRecordWire): AlarmRecord {
  return {
    event_id: Number(a.event_id ?? a.id ?? 0),
    dev_number: a.dev_number,
    cfg_id: Number(a.cfg_id ?? 0),
    alarm_name: a.alarm_name ?? a.alarm_msg ?? '告警',
    value: Number(a.value ?? a.alarm_value ?? 0),
    limit: Number(a.limit ?? 0),
    severity: a.severity ?? 'warning',
    ts: String(a.ts ?? a.triggered_at ?? new Date(0).toISOString()),
    acked_at: a.acked_at ?? a.reset_at ?? undefined,
    acked_by: a.acked_by,
  }
}

export async function listAlarmConfigs(devNumber: string): Promise<AlarmConfig[]> {
  const { data } = await apiClient.get(`/devices/${devNumber}/alarms/configs`)
  return itemsOf(data.data as AlarmConfigWire[] | ListEnvelope<AlarmConfigWire>).map(toAlarmConfig)
}

export async function createAlarmConfig(
  devNumber: string,
  p: Omit<AlarmConfig, 'cfg_id'>,
): Promise<AlarmConfig> {
  const { data } = await apiClient.post(
    `/devices/${devNumber}/alarms/configs`,
    toAlarmConfigCreatePayload(p),
  )
  return toAlarmConfig(data.data as AlarmConfigWire)
}

export async function updateAlarmConfig(
  devNumber: string,
  cfgId: number,
  p: Partial<AlarmConfig>,
): Promise<AlarmConfig> {
  const { data } = await apiClient.put(
    `/devices/${devNumber}/alarms/configs/${cfgId}`,
    toAlarmConfigUpdatePayload(p),
  )
  return toAlarmConfig(data.data as AlarmConfigWire)
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
  const { acked, cursor, ...rest } = params ?? {}
  const { data } = await apiClient.get('/alarms', {
    params: {
      ...rest,
      active_only: acked === false ? true : undefined,
      offset: cursor ? Number(cursor) : undefined,
    },
  })
  const payload = data.data as ListEnvelope<AlarmRecordWire>
  const items = itemsOf(payload).map(toAlarmRecord)
  return {
    items,
    next_cursor: payload.next_cursor ?? null,
  }
}

export async function resetAlarm(alarmId: number): Promise<void> {
  await apiClient.put(`/alarms/${alarmId}/reset`, {})
}

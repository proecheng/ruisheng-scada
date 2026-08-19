import { apiClient } from '@/api/client'

export type AlarmType = '>' | '<' | '=' | '!=' | 'LX'

export interface AlarmConfig {
  cfg_id: number
  point_id: number
  reg_bit?: number | null
  alarm_name: string
  alarm_type: AlarmType
  limit: number
  relation_point_id?: number | null
  relation_reg_bit?: number | null
  relation_alarm_type?: AlarmType | null
  relation_limit_value?: number | null
  severity: 'info' | 'warning' | 'critical'
  channels: string[]
}

export type NotificationChannel =
  | 'wechat'
  | 'email'
  | 'sms_custom_http'
  | 'voice_custom_http'

export interface AlarmSubscription {
  id: number
  alarm_cfg_id: number
  user_name: string
  channel: NotificationChannel
  created_at: string
}

export interface DeliveryAudit {
  id: number
  channel: NotificationChannel
  status: 'pending' | 'retry' | 'leased' | 'sent' | 'failed' | 'skipped'
  attempt_count: number
  last_error_class?: string
  created_at: string
  updated_at: string
  sent_at?: string
  attempts: DeliveryAttemptAudit[]
}

export interface DeliveryAttemptAudit {
  attempt_no: number
  outcome: 'sent' | 'retry' | 'failed' | 'skipped' | 'stale'
  error_class?: string
  http_status?: number
  retry_after_sec?: number
  started_at: string
  finished_at: string
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
  reg_bit?: number | null
  relation_point_id?: number | null
  relation_reg_bit?: number | null
  relation_alarm_type?: AlarmType | null
  relation_limit_value?: number | null
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
  limit_value?: number
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
    reg_bit: c.reg_bit ?? null,
    alarm_name: c.alarm_name,
    alarm_type: c.alarm_type,
    limit: Number(c.limit ?? c.limit_value ?? 0),
    relation_point_id: c.relation_point_id ?? null,
    relation_reg_bit: c.relation_reg_bit ?? null,
    relation_alarm_type: c.relation_alarm_type ?? null,
    relation_limit_value: c.relation_limit_value ?? null,
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
    relation_point_id: c.relation_point_id ?? null,
    relation_reg_bit: c.relation_reg_bit ?? null,
    relation_alarm_type: c.relation_alarm_type ?? null,
    relation_limit_value: c.relation_limit_value ?? null,
    enable: true,
  }
}

function toAlarmConfigUpdatePayload(c: Partial<AlarmConfig>) {
  const payload: Record<string, unknown> = {
    alarm_name: c.alarm_name,
    alarm_type: c.alarm_type,
    limit_value: c.limit,
  }
  if (
    'relation_point_id' in c ||
    'relation_reg_bit' in c ||
    'relation_alarm_type' in c ||
    'relation_limit_value' in c
  ) {
    payload.relation_point_id = c.relation_point_id ?? null
    payload.relation_reg_bit = c.relation_reg_bit ?? null
    payload.relation_alarm_type = c.relation_alarm_type ?? null
    payload.relation_limit_value = c.relation_limit_value ?? null
  }
  return payload
}

function toAlarmRecord(a: AlarmRecordWire): AlarmRecord {
  return {
    event_id: Number(a.event_id ?? a.id ?? 0),
    dev_number: a.dev_number,
    cfg_id: Number(a.cfg_id ?? 0),
    alarm_name: a.alarm_name ?? a.alarm_msg ?? '告警',
    value: Number(a.value ?? a.alarm_value ?? 0),
    limit: Number(a.limit ?? a.limit_value ?? 0),
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

export async function listAlarmSubscriptions(
  devNumber: string,
  cfgId: number,
): Promise<AlarmSubscription[]> {
  const { data } = await apiClient.get(
    `/devices/${devNumber}/alarms/configs/${cfgId}/subscriptions`,
  )
  return itemsOf(data.data as ListEnvelope<AlarmSubscription>)
}

export async function createAlarmSubscription(
  devNumber: string,
  cfgId: number,
  userName: string,
  channel: NotificationChannel,
): Promise<AlarmSubscription> {
  const { data } = await apiClient.post(
    `/devices/${devNumber}/alarms/configs/${cfgId}/subscriptions`,
    { user_name: userName, channel },
  )
  return data.data as AlarmSubscription
}

export async function deleteAlarmSubscription(
  devNumber: string,
  cfgId: number,
  subscriptionId: number,
): Promise<void> {
  await apiClient.delete(
    `/devices/${devNumber}/alarms/configs/${cfgId}/subscriptions/${subscriptionId}`,
  )
}

export async function listDeliveryAudit(
  devNumber: string,
  cfgId: number,
): Promise<DeliveryAudit[]> {
  const { data } = await apiClient.get(
    `/devices/${devNumber}/alarms/configs/${cfgId}/delivery-audit`,
  )
  return itemsOf(data.data as ListEnvelope<DeliveryAudit>)
}

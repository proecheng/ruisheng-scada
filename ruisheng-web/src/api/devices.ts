import { apiClient } from '@/api/client'

export interface Device {
  id?: number
  dev_number: string
  dev_ser_number?: string
  dev_name: string
  dev_type?: string | null
  modbus_addr?: number
  baud_rate?: number | null
  update_interval_decisec?: number
  state: 'online' | 'offline' | 'warning'
  is_online?: boolean
  group_company?: string | null
  company?: string
  department?: string
  dtu_ip?: string
  dtu_port?: number
  last_state?: string
  update_flag?: number
  iccid?: string
  owner_user_name?: string
}

export interface DeviceCreatePayload {
  dev_number: string
  dev_ser_number: string
  modbus_addr: number
  iccid?: string
  dev_name?: string
  dev_type?: string
  baud_rate?: number
  update_interval_decisec?: number
  group_company?: string
  company?: string
  department?: string
}

export type DeviceUpdatePayload = Partial<
  Pick<
    DeviceCreatePayload,
    'dev_name' | 'dev_type' | 'baud_rate' | 'update_interval_decisec' | 'group_company' | 'company' | 'department'
  >
>

export interface RealtimePoint {
  point_id: number
  point_name?: string
  value: number
  ts: string
  unit?: string
}

export interface RealtimeSnapshot {
  dev_number: string
  points: RealtimePoint[]
}

export interface HistoryQuery {
  point_id: number
  from: string
  to: string
  sample_interval_s?: number
  cursor?: string
}

export interface HistoryPage {
  points: Array<{ ts: string; value: number }>
  next_cursor: string | null
  downsampled?: boolean
  sample_interval_s?: number
}

interface ListEnvelope<T> {
  items: T[]
  total?: number
}

interface DeviceWire {
  id?: number
  dev_number: string
  dev_ser_number?: string
  dev_name?: string | null
  dev_type?: string | null
  modbus_addr?: number
  baud_rate?: number | null
  update_interval_decisec?: number
  state?: Device['state']
  is_online?: boolean
  group_company?: string | null
  company?: string
  department?: string
  dtu_ip?: string
  dtu_port?: number
  last_state?: string
  update_flag?: number
  iccid?: string
  owner_user_name?: string
}

interface RealtimePointWire extends Partial<RealtimePoint> {
  rt_value?: number
  recorded_at?: string
  point_unit?: string | null
  user_point_name?: string | null
}

interface HistoryWire {
  rows?: Array<{
    ts?: string
    recorded_at?: string
    value?: number
    rt_value?: number
  }>
  points?: Array<{ ts: string; value: number }>
  next_offset?: number | null
  next_cursor?: string | null
  downsampled?: boolean
  sample_interval_s?: number
}

function itemsOf<T>(payload: T[] | ListEnvelope<T> | undefined): T[] {
  if (Array.isArray(payload)) return payload
  return payload?.items ?? []
}

function toDevice(d: DeviceWire): Device {
  return {
    ...d,
    dev_name: d.dev_name ?? d.dev_number,
    state: d.state ?? (d.is_online ? 'online' : 'offline'),
  }
}

function toRealtimePoint(p: RealtimePointWire): RealtimePoint {
  return {
    point_id: Number(p.point_id ?? 0),
    point_name: p.point_name ?? p.user_point_name ?? undefined,
    value: Number(p.value ?? p.rt_value ?? 0),
    ts: String(p.ts ?? p.recorded_at ?? new Date(0).toISOString()),
    unit: p.unit ?? p.point_unit ?? undefined,
  }
}

export async function listDevices(params?: {
  company?: string
  department?: string
  q?: string
}): Promise<Device[]> {
  const { data } = await apiClient.get('/devices', { params })
  return itemsOf(data.data as DeviceWire[] | ListEnvelope<DeviceWire>).map(toDevice)
}

export async function getDevice(dev_number: string): Promise<Device> {
  const { data } = await apiClient.get(`/devices/${dev_number}`)
  return toDevice(data.data as DeviceWire)
}

export async function createDevice(payload: DeviceCreatePayload): Promise<Device> {
  const { data } = await apiClient.post('/devices', payload)
  return toDevice(data.data as DeviceWire)
}

export async function updateDevice(dev_number: string, payload: DeviceUpdatePayload): Promise<Device> {
  const { data } = await apiClient.put(`/devices/${dev_number}`, payload)
  return toDevice(data.data as DeviceWire)
}

export async function deleteDevice(dev_number: string): Promise<void> {
  await apiClient.delete(`/devices/${dev_number}`)
}

export async function getRealtime(dev_number: string): Promise<RealtimeSnapshot> {
  const { data } = await apiClient.get(`/devices/${dev_number}/realtime`)
  const snap = data.data as { dev_number: string; points: RealtimePointWire[] }
  return {
    dev_number: snap.dev_number,
    points: (snap.points ?? []).map(toRealtimePoint),
  }
}

export async function getHistory(dev_number: string, q: HistoryQuery): Promise<HistoryPage> {
  const { data } = await apiClient.get(`/devices/${dev_number}/history`, { params: q })
  const page = data.data as HistoryWire
  const points =
    page.points ??
    (page.rows ?? []).map((p) => ({
      ts: String(p.ts ?? p.recorded_at ?? new Date(0).toISOString()),
      value: Number(p.value ?? p.rt_value ?? 0),
    }))
  return {
    points,
    next_cursor:
      page.next_cursor ?? (page.next_offset === null || page.next_offset === undefined
        ? null
        : String(page.next_offset)),
    downsampled: page.downsampled,
    sample_interval_s: page.sample_interval_s,
  }
}

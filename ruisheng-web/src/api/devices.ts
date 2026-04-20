import { apiClient } from '@/api/client'

export interface Device {
  dev_number: string
  dev_name: string
  state: 'online' | 'offline' | 'warning'
  company?: string
  department?: string
  dtu_ip?: string
  dtu_port?: number
  last_state?: string
  update_flag?: number
  iccid?: string
  owner_user_name?: string
}

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

export async function listDevices(params?: {
  company?: string
  department?: string
  q?: string
}): Promise<Device[]> {
  const { data } = await apiClient.get('/devices', { params })
  return data.data as Device[]
}

export async function getDevice(dev_number: string): Promise<Device> {
  const { data } = await apiClient.get(`/devices/${dev_number}`)
  return data.data as Device
}

export async function createDevice(payload: Partial<Device> & { dev_number: string }): Promise<Device> {
  const { data } = await apiClient.post('/devices', payload)
  return data.data as Device
}

export async function updateDevice(dev_number: string, payload: Partial<Device>): Promise<Device> {
  const { data } = await apiClient.put(`/devices/${dev_number}`, payload)
  return data.data as Device
}

export async function deleteDevice(dev_number: string): Promise<void> {
  await apiClient.delete(`/devices/${dev_number}`)
}

export async function getRealtime(dev_number: string): Promise<RealtimeSnapshot> {
  const { data } = await apiClient.get(`/devices/${dev_number}/realtime`)
  return data.data as RealtimeSnapshot
}

export async function getHistory(dev_number: string, q: HistoryQuery): Promise<HistoryPage> {
  const { data } = await apiClient.get(`/devices/${dev_number}/history`, { params: q })
  return data.data as HistoryPage
}

import { apiClient } from '@/api/client'
import type { PointConfig } from '@/api/points'

export interface DeviceTemplate {
  id: number
  name: string
  dev_type?: string | null
  payload: {
    points: PointConfig[]
  }
}

interface TemplateWire {
  id: number
  name: string
  dev_type?: string | null
  payload?: {
    points?: Array<Record<string, unknown>>
  }
}

interface ListEnvelope<T> {
  items: T[]
}

function itemsOf<T>(payload: T[] | ListEnvelope<T> | undefined): T[] {
  if (Array.isArray(payload)) return payload
  return payload?.items ?? []
}

function toPointConfig(row: Record<string, unknown>): PointConfig {
  return {
    point_id: Number(row.id ?? row.point_id ?? 0),
    point_name: String(row.user_point_name ?? row.point_name ?? ''),
    register_address: Number(row.point_number ?? row.register_address ?? 0),
    fun_code: Number(row.fun_code ?? 3) as 1 | 2 | 3 | 4,
    dev_addr: Number(row.dev_addr ?? 1),
    r_bit: row.r_bit === null || row.r_bit === undefined || row.r_bit === '' ? null : Number(row.r_bit),
    data_type: String(row.value_type ?? row.data_type ?? '字') as PointConfig['data_type'],
    raw_ratio: Number(row.point_ratio ?? row.raw_ratio ?? 1),
    raw_offset: Number(row.point_offset ?? row.raw_offset ?? 0),
    ratio: Number(row.user_ratio ?? row.ratio ?? 1),
    offset: Number(row.user_point_offset ?? row.offset ?? 0),
    unit: row.point_unit === null || row.point_unit === undefined ? undefined : String(row.point_unit),
    min_value: row.min_value === null || row.min_value === undefined || row.min_value === '' ? null : Number(row.min_value),
    max_value: row.max_value === null || row.max_value === undefined || row.max_value === '' ? null : Number(row.max_value),
    show: row.show === undefined ? true : Number(row.show) === 1 || row.show === true,
  }
}

function toWirePoint(point: PointConfig): Record<string, unknown> {
  return {
    point_name: point.point_name,
    user_point_name: point.point_name,
    point_number: point.register_address,
    fun_code: point.fun_code,
    dev_addr: point.dev_addr,
    r_bit: point.data_type === 'bit' && point.fun_code !== 1 && point.fun_code !== 2 ? point.r_bit : undefined,
    value_type: point.data_type,
    point_unit: point.unit || undefined,
    point_ratio: point.raw_ratio,
    point_offset: point.raw_offset,
    user_ratio: point.ratio,
    user_point_offset: point.offset,
    min_value: point.min_value ?? undefined,
    max_value: point.max_value ?? undefined,
    show: point.show === false ? 0 : 1,
  }
}

function toTemplate(t: TemplateWire): DeviceTemplate {
  return {
    id: t.id,
    name: t.name,
    dev_type: t.dev_type ?? null,
    payload: {
      points: (t.payload?.points ?? []).map(toPointConfig),
    },
  }
}

export async function listDeviceTemplates(): Promise<DeviceTemplate[]> {
  const { data } = await apiClient.get('/device-templates')
  return itemsOf(data.data as TemplateWire[] | ListEnvelope<TemplateWire>).map(toTemplate)
}

export async function createDeviceTemplate(payload: {
  name: string
  dev_type?: string
  points?: PointConfig[]
}): Promise<DeviceTemplate> {
  const { data } = await apiClient.post('/device-templates', {
    name: payload.name,
    dev_type: payload.dev_type,
    payload: { points: (payload.points ?? []).map(toWirePoint) },
  })
  return toTemplate(data.data as TemplateWire)
}

export async function updateDeviceTemplate(
  id: number,
  payload: { name?: string; dev_type?: string; points?: PointConfig[] },
): Promise<DeviceTemplate> {
  const { data } = await apiClient.put(`/device-templates/${id}`, {
    name: payload.name,
    dev_type: payload.dev_type,
    payload: payload.points === undefined ? undefined : { points: payload.points.map(toWirePoint) },
  })
  return toTemplate(data.data as TemplateWire)
}

export async function deleteDeviceTemplate(id: number): Promise<void> {
  await apiClient.delete(`/device-templates/${id}`)
}

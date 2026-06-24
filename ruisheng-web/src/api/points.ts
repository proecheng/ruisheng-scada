import { apiClient } from '@/api/client'

export interface PointConfig {
  point_id: number
  point_name: string
  ratio: number
  offset: number
  unit?: string
  data_type?: string
  precision?: number
}

interface ListEnvelope<T> {
  items: T[]
}

interface PointWire {
  id: number
  point_id?: number
  point_name: string
  user_point_name?: string | null
  point_number: number
  fun_code: number
  dev_addr: number
  value_type: string
  point_unit?: string | null
  point_ratio: number
  point_offset: number
  user_ratio: number
  user_point_offset: number
}

function itemsOf<T>(payload: T[] | ListEnvelope<T> | undefined): T[] {
  if (Array.isArray(payload)) return payload
  return payload?.items ?? []
}

function toPoint(p: PointWire): PointConfig {
  return {
    point_id: p.id ?? p.point_id ?? p.point_number,
    point_name: p.user_point_name ?? p.point_name,
    ratio: p.user_ratio ?? p.point_ratio,
    offset: p.user_point_offset ?? p.point_offset,
    unit: p.point_unit ?? undefined,
    data_type: p.value_type,
  }
}

function toCreatePayload(p: PointConfig) {
  return {
    point_name: p.point_name,
    user_point_name: p.point_name,
    point_number: p.point_id,
    fun_code: 3,
    dev_addr: 1,
    value_type: p.data_type ?? '字',
    point_unit: p.unit || undefined,
    user_ratio: p.ratio,
    user_point_offset: p.offset,
  }
}

function toUpdatePayload(p: Partial<PointConfig>) {
  return {
    user_point_name: p.point_name,
    point_unit: p.unit,
    user_ratio: p.ratio,
    user_point_offset: p.offset,
  }
}

export async function listPoints(devNumber: string): Promise<PointConfig[]> {
  const { data } = await apiClient.get(`/devices/${devNumber}/points`)
  return itemsOf(data.data as PointWire[] | ListEnvelope<PointWire>).map(toPoint)
}

export async function createPoint(devNumber: string, p: PointConfig): Promise<PointConfig> {
  const { data } = await apiClient.post(`/devices/${devNumber}/points`, toCreatePayload(p))
  return toPoint(data.data as PointWire)
}

export async function updatePoint(
  devNumber: string,
  pointId: number,
  p: Partial<PointConfig>,
): Promise<PointConfig> {
  const { data } = await apiClient.put(`/devices/${devNumber}/points/${pointId}`, toUpdatePayload(p))
  return toPoint(data.data as PointWire)
}

export async function deletePoint(devNumber: string, pointId: number): Promise<void> {
  await apiClient.delete(`/devices/${devNumber}/points/${pointId}`)
}

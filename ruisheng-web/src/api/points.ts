import { apiClient } from '@/api/client'

export interface PointConfig {
  point_id: number
  point_name: string
  register_address: number
  fun_code: 1 | 2 | 3 | 4
  dev_addr: number
  r_bit?: number | null
  data_type: '字' | '双字' | 'bit'
  raw_ratio: number
  raw_offset: number
  ratio: number
  offset: number
  unit?: string
  precision?: number
  min_value?: number | null
  max_value?: number | null
  show?: boolean
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
  r_bit?: number | null
  value_type: '字' | '双字' | 'bit'
  point_unit?: string | null
  point_ratio: number
  point_offset: number
  user_ratio: number
  user_point_offset: number
  min_value?: number | null
  max_value?: number | null
  show?: number
}

function itemsOf<T>(payload: T[] | ListEnvelope<T> | undefined): T[] {
  if (Array.isArray(payload)) return payload
  return payload?.items ?? []
}

function toPoint(p: PointWire): PointConfig {
  return {
    point_id: p.id ?? p.point_id ?? p.point_number,
    point_name: p.user_point_name ?? p.point_name,
    register_address: p.point_number,
    fun_code: p.fun_code as 1 | 2 | 3 | 4,
    dev_addr: p.dev_addr,
    r_bit: p.r_bit ?? null,
    data_type: p.value_type,
    raw_ratio: p.point_ratio,
    raw_offset: p.point_offset,
    ratio: p.user_ratio ?? p.point_ratio,
    offset: p.user_point_offset ?? p.point_offset,
    unit: p.point_unit ?? undefined,
    min_value: p.min_value ?? null,
    max_value: p.max_value ?? null,
    show: p.show === undefined ? true : p.show === 1,
  }
}

function toCreatePayload(p: PointConfig) {
  return {
    point_name: p.point_name,
    user_point_name: p.point_name,
    point_number: p.register_address,
    fun_code: p.fun_code,
    dev_addr: p.dev_addr,
    r_bit: p.data_type === 'bit' && p.fun_code !== 1 && p.fun_code !== 2 ? p.r_bit : undefined,
    value_type: p.data_type,
    point_unit: p.unit || undefined,
    point_ratio: p.raw_ratio,
    point_offset: p.raw_offset,
    user_ratio: p.ratio,
    user_point_offset: p.offset,
    min_value: p.min_value ?? undefined,
    max_value: p.max_value ?? undefined,
    show: p.show === false ? 0 : 1,
  }
}

function toUpdatePayload(p: Partial<PointConfig>) {
  return {
    point_name: p.point_name,
    user_point_name: p.point_name,
    point_number: p.register_address,
    fun_code: p.fun_code,
    dev_addr: p.dev_addr,
    r_bit: p.data_type === 'bit' && p.fun_code !== 1 && p.fun_code !== 2 ? p.r_bit : null,
    value_type: p.data_type,
    point_unit: p.unit,
    point_ratio: p.raw_ratio,
    point_offset: p.raw_offset,
    user_ratio: p.ratio,
    user_point_offset: p.offset,
    min_value: p.min_value,
    max_value: p.max_value,
    show: p.show === undefined ? undefined : p.show ? 1 : 0,
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

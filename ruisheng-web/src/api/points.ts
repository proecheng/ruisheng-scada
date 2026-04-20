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

export async function listPoints(devNumber: string): Promise<PointConfig[]> {
  const { data } = await apiClient.get(`/devices/${devNumber}/points`)
  return data.data as PointConfig[]
}

export async function createPoint(devNumber: string, p: PointConfig): Promise<PointConfig> {
  const { data } = await apiClient.post(`/devices/${devNumber}/points`, p)
  return data.data as PointConfig
}

export async function updatePoint(
  devNumber: string,
  pointId: number,
  p: Partial<PointConfig>,
): Promise<PointConfig> {
  const { data } = await apiClient.put(`/devices/${devNumber}/points/${pointId}`, p)
  return data.data as PointConfig
}

export async function deletePoint(devNumber: string, pointId: number): Promise<void> {
  await apiClient.delete(`/devices/${devNumber}/points/${pointId}`)
}

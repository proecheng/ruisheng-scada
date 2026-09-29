import { apiClient } from '@/api/client'

export interface DoInfo {
  supported: boolean
  config_version: number
  channels: number[]
  enabled: boolean
  sample: { org_value: number | null; recorded_at: string } | null
}
export interface DoExecution {
  reason?: string
  before?: number
  readback?: number
  channels?: { channel: number; high: boolean; phase: string }[]
}
export interface DoResult {
  cmd_id: string
  result: 'pending' | 'success' | 'failed' | 'timeout' | 'cancelled'
  execution?: DoExecution | null
}
export async function getDoInfo(dev: string): Promise<DoInfo> {
  const { data } = await apiClient.get(`/devices/${dev}/do-control`)
  return data.data as DoInfo
}
export async function setDoLevels(dev: string, channels: { number: number; high: boolean }[], version: number, highRisk: boolean, otp: string): Promise<{ cmd_id: string }> {
  const { data } = await apiClient.post(`/devices/${dev}/do-control`, {
    channels, config_version: version, high_risk: highRisk,
  }, { headers: otp ? { 'X-OTP-Code': otp } : {} })
  return data.data as { cmd_id: string }
}
export async function getDoResult(dev: string, cmd: string): Promise<DoResult> {
  const { data } = await apiClient.get(`/devices/${dev}/do-commands/${cmd}`)
  return data.data as DoResult
}

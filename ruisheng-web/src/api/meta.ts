import { apiClient } from '@/api/client'

export interface MetaVersion {
  api_version: string
  build_hash: string
  build_time: string
  db_schema_version?: string
}

export interface ReadyCheck {
  ok: boolean
  components: Record<string, { ok: boolean; detail?: string }>
}

export async function getVersion(): Promise<MetaVersion> {
  const { data } = await apiClient.get('/meta/version')
  return data.data as MetaVersion
}

export async function getReady(): Promise<ReadyCheck> {
  const { data } = await apiClient.get('/health/ready')
  return data.data as ReadyCheck
}

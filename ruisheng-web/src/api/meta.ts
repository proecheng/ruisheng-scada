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
  const payload = data.data as Partial<MetaVersion> & { version?: string }
  return {
    api_version: payload.api_version ?? payload.version ?? 'unknown',
    build_hash: payload.build_hash ?? 'unknown',
    build_time: payload.build_time ?? '',
    db_schema_version: payload.db_schema_version,
  }
}

export async function getReady(): Promise<ReadyCheck> {
  const { data } = await apiClient.get('/health/ready')
  const payload = data.data as Partial<ReadyCheck> & { status?: string }
  if (payload.components) return payload as ReadyCheck
  return {
    ok: payload.status === 'ready',
    components: {
      api: { ok: payload.status === 'ready', detail: payload.status },
    },
  }
}

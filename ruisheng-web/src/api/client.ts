import axios, { AxiosError, type AxiosInstance, type InternalAxiosRequestConfig } from 'axios'
import { generateUlid } from '@/utils/ulid'
import { mapErrCode } from '@/utils/errors'
import type { ApiResponse } from '@/api/types'
import { useDiagStore } from '@/stores/diag'

const BASE = import.meta.env.VITE_API_BASE ?? '/api'

let authToken: string | null = null
const AUTH_EXPIRED_EVENT = 'ruisheng:auth-expired'
export function setAuthToken(token: string | null): void {
  authToken = token
}
export function getAuthToken(): string | null {
  return authToken
}
function notifyAuthExpired(): void {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT))
  }
}

type ApiClientError = Error & { code: number; hint?: string; traceId?: string }

function isLoginRequest(config?: { url?: string; ruishengAuthRequest?: boolean }): boolean {
  if (config?.ruishengAuthRequest) return true
  if (!config?.url) return false
  try {
    const path = new URL(config.url, 'http://ruisheng.invalid').pathname.replace(/\/+$/, '')
    return path === '/auth/login' || path === '/api/auth/login'
  } catch {
    return false
  }
}

function toLoginCredentialsError(traceId?: string): ApiClientError {
  const error = new Error('用户名或密码错误') as ApiClientError
  error.code = -101
  error.traceId = traceId
  return error
}

function toApiError(body: ApiResponse, loginRequest: boolean): ApiClientError {
  const traceId = body.trace_id ?? body.transid
  if (loginRequest && body.code === -101) return toLoginCredentialsError(traceId)
  const err = mapErrCode(body.code, body.message ?? body.msg ?? '请求失败')
  const error = new Error(err.headline) as ApiClientError
  error.code = body.code
  error.hint = err.hint
  error.traceId = traceId
  return error
}

export const apiClient: AxiosInstance = axios.create({
  baseURL: BASE,
  timeout: 30000,
})

apiClient.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const traceId = generateUlid()
  config.headers.set('X-Trace-Id', traceId)
  const method = (config.method ?? 'get').toUpperCase()
  if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
    if (!config.headers.get('Idempotency-Key')) {
      config.headers.set('Idempotency-Key', generateUlid())
    }
  }
  if (authToken) config.headers.set('Authorization', `Bearer ${authToken}`)
  return config
})

apiClient.interceptors.response.use(
  (response) => {
    const body = response.data as ApiResponse
    if (body && typeof body === 'object' && 'code' in body && body.code !== 0) {
      const loginRequest = isLoginRequest(response.config)
      if (body.code === -101 && !loginRequest) notifyAuthExpired()
      throw toApiError(body, loginRequest)
    }
    return response
  },
  (error: AxiosError<ApiResponse>) => {
    const body = error.response?.data
    const loginRequest = isLoginRequest(error.config)
    if (body && typeof body === 'object' && 'code' in body) {
      if (!loginRequest && (body.code === -101 || error.response?.status === 401)) {
        notifyAuthExpired()
      }
      return Promise.reject(toApiError(body, loginRequest))
    }
    if (error.response?.status === 401 && loginRequest) {
      return Promise.reject(toLoginCredentialsError())
    }
    if (error.response?.status === 401 && !loginRequest) notifyAuthExpired()
    return Promise.reject(error)
  },
)

// --- Diag recording (runs after ApiResponse interceptors) ---
apiClient.interceptors.request.use((config) => {
  ;(config as { __start_ts?: number }).__start_ts = Date.now()
  return config
})

apiClient.interceptors.response.use(
  (response) => {
    try {
      const diag = useDiagStore()
      const start = (response.config as { __start_ts?: number }).__start_ts ?? Date.now()
      diag.record({
        at: new Date().toISOString(),
        kind: 'api',
        label: `${response.config.method?.toUpperCase()} ${response.config.url}`,
        traceId: response.headers?.['x-trace-id'] as string | undefined,
        durationMs: Date.now() - start,
      })
    } catch {
      /* pinia may not be active during boot */
    }
    return response
  },
  (error) => {
    try {
      const diag = useDiagStore()
      diag.record({
        at: new Date().toISOString(),
        kind: 'error',
        label: `${(error as { config?: { method?: string; url?: string } }).config?.method?.toUpperCase() ?? '?'} ${(error as { config?: { method?: string; url?: string } }).config?.url ?? '?'}`,
        detail: error instanceof Error ? error.message : String(error),
      })
    } catch {
      /* */
    }
    return Promise.reject(error)
  },
)

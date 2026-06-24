import { apiClient } from '@/api/client'
import type { Authority, Session } from '@/stores/auth'

export interface LoginRequest {
  user_name: string
  password?: string
  phone?: string
  sms_code?: string
}

interface BackendSession {
  access_token: string
  refresh_token: string
  access_ttl_sec?: number
  user_name?: string
  role?: Authority
  authority?: Authority
  usr_group?: string
  control_authority?: number
}

function toSession(payload: BackendSession | Session): Session {
  if ('user' in payload && payload.user) return payload
  const backend = payload as BackendSession
  const authority = backend.role ?? backend.authority ?? 'User'
  return {
    access_token: backend.access_token,
    refresh_token: backend.refresh_token,
    user: {
      user_name: backend.user_name ?? '',
      authority,
      usr_group: backend.usr_group ?? '',
      control_authority: backend.control_authority,
    },
  }
}

export async function login(req: LoginRequest): Promise<Session> {
  const { data } = await apiClient.post('/auth/login', req)
  return toSession(data.data as BackendSession | Session)
}

export async function refresh(refreshToken: string): Promise<Session> {
  const { data } = await apiClient.post('/auth/refresh', { refresh_token: refreshToken })
  return toSession(data.data as BackendSession | Session)
}

export async function logout(): Promise<void> {
  await apiClient.post('/auth/logout', {})
}

export async function smsSend(req: { phone: string; scene: string }): Promise<void> {
  await apiClient.post('/auth/sms/send', {
    phone_number: req.phone,
    action: req.scene,
    channel: 'sms',
  })
}

export async function otpSend(req: {
  channel: 'sms' | 'email' | 'wechat'
  action?: string
}): Promise<void> {
  await apiClient.post('/auth/otp/send', {
    channel: req.channel,
    action: req.action ?? 'control',
  })
}

export async function register(req: {
  user_name: string
  password: string
  phone: string
  sms_code: string
}): Promise<{ user_name: string }> {
  const { data } = await apiClient.post('/auth/register', {
    user_name: req.user_name,
    password: req.password,
    otp_code: req.sms_code,
  })
  return data.data as { user_name: string }
}

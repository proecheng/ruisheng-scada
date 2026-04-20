import { apiClient } from '@/api/client'
import type { Session } from '@/stores/auth'

export interface LoginRequest {
  user_name: string
  password?: string
  phone?: string
  sms_code?: string
}

export async function login(req: LoginRequest): Promise<Session> {
  const { data } = await apiClient.post('/auth/login', req)
  return data.data as Session
}

export async function refresh(refreshToken: string): Promise<Session> {
  const { data } = await apiClient.post('/auth/refresh', { refresh_token: refreshToken })
  return data.data as Session
}

export async function logout(): Promise<void> {
  await apiClient.post('/auth/logout', {})
}

export async function smsSend(req: { phone: string; scene: string }): Promise<void> {
  await apiClient.post('/auth/sms/send', req)
}

export async function otpSend(req: { channel: 'sms' | 'email'; target: string }): Promise<void> {
  await apiClient.post('/auth/otp/send', req)
}

export async function register(req: {
  user_name: string
  password: string
  phone: string
  sms_code: string
}): Promise<Session> {
  const { data } = await apiClient.post('/auth/register', req)
  return data.data as Session
}

import { apiClient } from '@/api/client'
import type { Authority } from '@/stores/auth'

export interface OrgUser {
  user_name: string
  display_name?: string
  authority: Authority
  usr_group: string
  company?: string
  department?: string
  control_authority: number
  deleted_at?: string
}

export interface WxGroup {
  usr_group: string
  group_name: string
  app_id?: string
}

export interface PhoneRecord {
  id: number
  phone: string
  label?: string
}

export interface EmailRecord {
  id: number
  email: string
  label?: string
}

export async function listUsers(): Promise<OrgUser[]> {
  const { data } = await apiClient.get('/orgs/users')
  return data.data as OrgUser[]
}

export async function createUser(u: Omit<OrgUser, 'deleted_at'> & { password: string }): Promise<OrgUser> {
  const { data } = await apiClient.post('/orgs/users', u)
  return data.data as OrgUser
}

export async function updateUser(userName: string, u: Partial<OrgUser>): Promise<OrgUser> {
  const { data } = await apiClient.put(`/orgs/users/${encodeURIComponent(userName)}`, u)
  return data.data as OrgUser
}

export async function deleteUser(userName: string): Promise<void> {
  await apiClient.delete(`/orgs/users/${encodeURIComponent(userName)}`)
}

export async function listWxGroups(): Promise<WxGroup[]> {
  const { data } = await apiClient.get('/orgs/wx_groups')
  return data.data as WxGroup[]
}

export async function listPhones(userName: string): Promise<PhoneRecord[]> {
  const { data } = await apiClient.get(`/orgs/users/${encodeURIComponent(userName)}/phones`)
  return data.data as PhoneRecord[]
}

export async function addPhone(userName: string, phone: string, label?: string): Promise<PhoneRecord> {
  const { data } = await apiClient.post(`/orgs/users/${encodeURIComponent(userName)}/phones`, { phone, label })
  return data.data as PhoneRecord
}

export async function deletePhone(userName: string, id: number): Promise<void> {
  await apiClient.delete(`/orgs/users/${encodeURIComponent(userName)}/phones/${id}`)
}

export async function listEmails(userName: string): Promise<EmailRecord[]> {
  const { data } = await apiClient.get(`/orgs/users/${encodeURIComponent(userName)}/emails`)
  return data.data as EmailRecord[]
}

export async function addEmail(userName: string, email: string, label?: string): Promise<EmailRecord> {
  const { data } = await apiClient.post(`/orgs/users/${encodeURIComponent(userName)}/emails`, { email, label })
  return data.data as EmailRecord
}

export async function deleteEmail(userName: string, id: number): Promise<void> {
  await apiClient.delete(`/orgs/users/${encodeURIComponent(userName)}/emails/${id}`)
}

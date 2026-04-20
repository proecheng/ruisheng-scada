import { apiClient } from '@/api/client'

export interface TimingPlan {
  id: number
  dev_number: string
  plan_name: string
  cron: string
  action: string
  enabled: boolean
  last_run_at?: string
  next_run_at?: string
}

export interface MaintenancePlan {
  id: number
  dev_number: string
  plan_name: string
  interval_days: number
  next_due_at: string
  last_done_at?: string
  owner_user_name: string
}

export interface MaintenanceAction {
  action_uuid: string
  plan_id: number
  dev_number: string
  note?: string
}

export async function listTimingPlans(dev_number?: string): Promise<TimingPlan[]> {
  const { data } = await apiClient.get('/plans/timing', { params: { dev_number } })
  return data.data as TimingPlan[]
}

export async function upsertTimingPlan(p: Partial<TimingPlan> & { dev_number: string }): Promise<TimingPlan> {
  if (p.id) {
    const { data } = await apiClient.put(`/plans/timing/${p.id}`, p)
    return data.data as TimingPlan
  } else {
    const { data } = await apiClient.post('/plans/timing', p)
    return data.data as TimingPlan
  }
}

export async function deleteTimingPlan(id: number): Promise<void> {
  await apiClient.delete(`/plans/timing/${id}`)
}

export async function listMaintenancePlans(dev_number?: string): Promise<MaintenancePlan[]> {
  const { data } = await apiClient.get('/plans/maintenance', { params: { dev_number } })
  return data.data as MaintenancePlan[]
}

export async function upsertMaintenancePlan(
  p: Partial<MaintenancePlan> & { dev_number: string },
): Promise<MaintenancePlan> {
  if (p.id) {
    const { data } = await apiClient.put(`/plans/maintenance/${p.id}`, p)
    return data.data as MaintenancePlan
  } else {
    const { data } = await apiClient.post('/plans/maintenance', p)
    return data.data as MaintenancePlan
  }
}

export async function deleteMaintenancePlan(id: number): Promise<void> {
  await apiClient.delete(`/plans/maintenance/${id}`)
}

export async function completeMaintenance(planId: number, action: MaintenanceAction): Promise<void> {
  await apiClient.post(`/plans/maintenance/${planId}/complete`, action)
}

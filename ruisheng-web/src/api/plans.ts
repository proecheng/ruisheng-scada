import { apiClient } from '@/api/client'

export interface TimingPlan {
  id: number
  dev_number: string
  action: string
  action_at: string
  repetition: number
  enabled: boolean
  updated_at?: string
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

interface ListEnvelope<T> {
  items: T[]
}

interface TimingPlanWire {
  id: number
  dev_number: string
  action_at: string
  action: number | string
  repetition?: number
  enable?: boolean
  enabled?: boolean
  created_at?: string
  updated_at?: string
}

interface MaintenancePlanWire {
  id: number
  dev_number: string
  plan_name: string
  description?: string | null
  interval_days: number
  next_due_at: string
  last_done_at?: string
  owner_user_name?: string
  enable?: boolean
  enabled?: boolean
}

function itemsOf<T>(payload: T[] | ListEnvelope<T> | undefined): T[] {
  if (Array.isArray(payload)) return payload
  return payload?.items ?? []
}

function actionName(action: number | string): string {
  if (typeof action === 'string') return action
  return ({ 1: 'start', 2: 'stop', 3: 'reset' } as Record<number, string>)[action] ?? String(action)
}

function actionCode(action: number | string | undefined): number {
  if (typeof action === 'number') return action
  return ({ start: 1, stop: 2, reset: 3 } as Record<string, number>)[action ?? 'start'] ?? 1
}

function toTimingPlan(p: TimingPlanWire): TimingPlan {
  return {
    id: p.id,
    dev_number: p.dev_number,
    action: actionName(p.action),
    action_at: p.action_at,
    repetition: p.repetition ?? 0,
    enabled: p.enabled ?? p.enable ?? false,
    updated_at: p.updated_at,
  }
}

function toTimingPayload(p: Partial<TimingPlan> & { dev_number: string }) {
  return {
    dev_number: p.dev_number,
    action_at: p.action_at ?? new Date().toISOString(),
    action: actionCode(p.action),
    repetition: p.repetition ?? 0,
    enable: p.enabled ?? true,
  }
}

function toTimingUpdatePayload(p: Partial<TimingPlan>) {
  return {
    action_at: p.action_at,
    action: p.action === undefined ? undefined : actionCode(p.action),
    repetition: p.repetition,
    enable: p.enabled,
  }
}

function toMaintenancePlan(p: MaintenancePlanWire): MaintenancePlan {
  return {
    id: p.id,
    dev_number: p.dev_number,
    plan_name: p.plan_name,
    interval_days: p.interval_days,
    next_due_at: p.next_due_at,
    last_done_at: p.last_done_at,
    owner_user_name: p.owner_user_name ?? '',
  }
}

function toMaintenancePayload(p: Partial<MaintenancePlan> & { dev_number: string }) {
  return {
    dev_number: p.dev_number,
    plan_name: p.plan_name,
    interval_days: p.interval_days,
    next_due_at: p.next_due_at,
    enable: true,
  }
}

export async function listTimingPlans(dev_number?: string): Promise<TimingPlan[]> {
  const { data } = await apiClient.get('/plans/timing', { params: { dev_number } })
  return itemsOf(data.data as TimingPlanWire[] | ListEnvelope<TimingPlanWire>).map(toTimingPlan)
}

export async function upsertTimingPlan(p: Partial<TimingPlan> & { dev_number: string }): Promise<TimingPlan> {
  if (p.id) {
    const { data } = await apiClient.put(`/plans/timing/${p.id}`, toTimingUpdatePayload(p))
    return toTimingPlan(data.data as TimingPlanWire)
  } else {
    const { data } = await apiClient.post('/plans/timing', toTimingPayload(p))
    return toTimingPlan(data.data as TimingPlanWire)
  }
}

export async function deleteTimingPlan(id: number): Promise<void> {
  await apiClient.delete(`/plans/timing/${id}`)
}

export async function listMaintenancePlans(dev_number?: string): Promise<MaintenancePlan[]> {
  const { data } = await apiClient.get('/plans/maintenance', { params: { dev_number } })
  return itemsOf(data.data as MaintenancePlanWire[] | ListEnvelope<MaintenancePlanWire>).map(
    toMaintenancePlan,
  )
}

export async function upsertMaintenancePlan(
  p: Partial<MaintenancePlan> & { dev_number: string },
): Promise<MaintenancePlan> {
  if (p.id) {
    const { data } = await apiClient.put(`/plans/maintenance/${p.id}`, toMaintenancePayload(p))
    return toMaintenancePlan(data.data as MaintenancePlanWire)
  } else {
    const { data } = await apiClient.post('/plans/maintenance', toMaintenancePayload(p))
    return toMaintenancePlan(data.data as MaintenancePlanWire)
  }
}

export async function deleteMaintenancePlan(id: number): Promise<void> {
  await apiClient.delete(`/plans/maintenance/${id}`)
}

export async function completeMaintenance(planId: number, action: MaintenanceAction): Promise<void> {
  await apiClient.post(`/plans/maintenance/${planId}/complete`, {
    action_uuid: action.action_uuid,
    dev_number: action.dev_number,
    note: action.note,
  })
}

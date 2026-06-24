import { apiClient } from '@/api/client'

export interface ScenePage {
  id: number
  page_name: string
  parent_id?: number
  pos_x: number
  pos_y: number
  sonpage_pic?: string
  owner_user_name?: string
  created_at?: string
}

export interface SceneView {
  id: number
  scene_page_id: number
  dev_number: string
  view_type: string
  shape: 'circle' | 'rect' | 'image'
  x: number
  y: number
  width: number
  height: number
  point_bindings?: Array<{ point_id: number; label: string }>
  company?: string
  department?: string
}

interface ListEnvelope<T> {
  items: T[]
}

interface ScenePageWire {
  id: number
  page_name: string
  parent_id?: number
  pos_x: number | string
  pos_y: number | string
  radius?: number | string
  sonpage_pic?: string
  owner_user_name?: string
  created_at?: string
}

interface SceneViewWire {
  id: number
  scene_page_id: number
  dev_number: string
  pos_x: number | string
  pos_y: number | string
  radius?: number | string
  owner_user_name?: string
  company?: string
  department?: string
}

function itemsOf<T>(payload: T[] | ListEnvelope<T> | undefined): T[] {
  if (Array.isArray(payload)) return payload
  return payload?.items ?? []
}

function toPage(p: ScenePageWire): ScenePage {
  return {
    id: p.id,
    page_name: p.page_name,
    parent_id: p.parent_id,
    pos_x: Number(p.pos_x),
    pos_y: Number(p.pos_y),
    sonpage_pic: p.sonpage_pic,
    owner_user_name: p.owner_user_name,
    created_at: p.created_at,
  }
}

function toView(v: SceneViewWire): SceneView {
  const radius = Number(v.radius ?? 20)
  return {
    id: v.id,
    scene_page_id: v.scene_page_id,
    dev_number: v.dev_number,
    view_type: 'default',
    shape: 'circle',
    x: Number(v.pos_x),
    y: Number(v.pos_y),
    width: radius * 2,
    height: radius * 2,
    company: v.company,
    department: v.department,
  }
}

export async function listPages(): Promise<ScenePage[]> {
  const { data } = await apiClient.get('/scenes/pages')
  return itemsOf(data.data as ScenePageWire[] | ListEnvelope<ScenePageWire>).map(toPage)
}

export async function createPage(p: Omit<ScenePage, 'id'>): Promise<ScenePage> {
  const { data } = await apiClient.post('/scenes/pages', {
    page_name: p.page_name,
    owner_user_name: p.owner_user_name,
    sonpage_pic: p.sonpage_pic,
    pos_x: p.pos_x,
    pos_y: p.pos_y,
    radius: 20,
  })
  return toPage(data.data as ScenePageWire)
}

export async function deletePage(id: number): Promise<void> {
  await apiClient.delete(`/scenes/pages/${id}`)
}

export async function listViews(pageId: number): Promise<SceneView[]> {
  const { data } = await apiClient.get(`/scenes/pages/${pageId}/views`)
  return itemsOf(data.data as SceneViewWire[] | ListEnvelope<SceneViewWire>).map(toView)
}

export async function createView(
  pageId: number,
  v: Omit<SceneView, 'id' | 'scene_page_id'>,
): Promise<SceneView> {
  const { data } = await apiClient.post(`/scenes/pages/${pageId}/views`, {
    dev_number: v.dev_number,
    pos_x: v.x,
    pos_y: v.y,
    radius: Math.max(v.width, v.height) / 2,
  })
  return toView(data.data as SceneViewWire)
}

export async function deleteView(pageId: number, viewId: number): Promise<void> {
  await apiClient.delete(`/scenes/pages/${pageId}/views/${viewId}`)
}

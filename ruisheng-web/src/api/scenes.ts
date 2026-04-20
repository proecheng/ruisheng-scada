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

export async function listPages(): Promise<ScenePage[]> {
  const { data } = await apiClient.get('/scenes/pages')
  return data.data as ScenePage[]
}

export async function createPage(p: Omit<ScenePage, 'id'>): Promise<ScenePage> {
  const { data } = await apiClient.post('/scenes/pages', p)
  return data.data as ScenePage
}

export async function deletePage(id: number): Promise<void> {
  await apiClient.delete(`/scenes/pages/${id}`)
}

export async function listViews(pageId: number): Promise<SceneView[]> {
  const { data } = await apiClient.get(`/scenes/pages/${pageId}/views`)
  return data.data as SceneView[]
}

export async function createView(
  pageId: number,
  v: Omit<SceneView, 'id' | 'scene_page_id'>,
): Promise<SceneView> {
  const { data } = await apiClient.post(`/scenes/pages/${pageId}/views`, v)
  return data.data as SceneView
}

export async function deleteView(pageId: number, viewId: number): Promise<void> {
  await apiClient.delete(`/scenes/pages/${pageId}/views/${viewId}`)
}

export interface ApiResponse<T = unknown> {
  code: number
  message: string
  data?: T
  trace_id?: string
}

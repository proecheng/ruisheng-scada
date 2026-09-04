export interface ApiResponse<T = unknown> {
  code: number
  message?: string
  msg?: string
  data?: T
  trace_id?: string
  transid?: string
}

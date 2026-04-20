import { apiClient } from '@/api/client'

export interface PayOrder {
  out_trade_no: string
  dev_number: string
  body: string
  total_fee: number
  trade_type: 'JSAPI' | 'NATIVE'
  code_url?: string
  jsapi_params?: Record<string, string>
  status: 'pending' | 'paid' | 'cancelled' | 'expired'
  created_at: string
}

export async function createOrder(req: {
  dev_number: string
  body: string
  total_fee: number
  trade_type: 'JSAPI' | 'NATIVE'
}): Promise<PayOrder> {
  const { data } = await apiClient.post('/pay/orders', req)
  return data.data as PayOrder
}

export async function listOrders(params?: { dev_number?: string; status?: string }): Promise<PayOrder[]> {
  const { data } = await apiClient.get('/pay/orders', { params })
  return data.data as PayOrder[]
}

export async function getOrder(out_trade_no: string): Promise<PayOrder> {
  const { data } = await apiClient.get(`/pay/orders/${out_trade_no}`)
  return data.data as PayOrder
}

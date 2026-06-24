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

interface PayOrderWire {
  out_trade_no: string
  openid?: string
  dev_number?: string
  body?: string
  description?: string
  total_fee?: number
  amount_fen?: number
  trade_type?: 'JSAPI' | 'NATIVE'
  code_url?: string
  jsapi_params?: Record<string, string>
  pay_state?: PayOrder['status']
  status?: PayOrder['status']
  created_at?: string
}

interface ListEnvelope<T> {
  items: T[]
}

function itemsOf<T>(payload: T[] | ListEnvelope<T> | undefined): T[] {
  if (Array.isArray(payload)) return payload
  return payload?.items ?? []
}

function toOrder(o: PayOrderWire): PayOrder {
  return {
    out_trade_no: o.out_trade_no,
    dev_number: o.dev_number ?? o.openid ?? '',
    body: o.body ?? o.description ?? '',
    total_fee: Number(o.total_fee ?? o.amount_fen ?? 0),
    trade_type: o.trade_type ?? 'NATIVE',
    code_url: o.code_url,
    jsapi_params: o.jsapi_params,
    status: o.status ?? o.pay_state ?? 'pending',
    created_at: o.created_at ?? new Date(0).toISOString(),
  }
}

export async function createOrder(req: {
  dev_number: string
  body: string
  total_fee: number
  trade_type: 'JSAPI' | 'NATIVE'
}): Promise<PayOrder> {
  const { data } = await apiClient.post('/pay/orders', {
    openid: req.dev_number,
    amount_fen: req.total_fee,
    description: req.body,
  })
  return toOrder(data.data as PayOrderWire)
}

export async function listOrders(params?: { dev_number?: string; status?: string }): Promise<PayOrder[]> {
  const { data } = await apiClient.get('/pay/orders', {
    params: { openid: params?.dev_number, status: params?.status },
  })
  return itemsOf(data.data as PayOrderWire[] | ListEnvelope<PayOrderWire>).map(toOrder)
}

export async function getOrder(out_trade_no: string): Promise<PayOrder> {
  const { data } = await apiClient.get(`/pay/orders/${out_trade_no}`)
  return toOrder(data.data as PayOrderWire)
}

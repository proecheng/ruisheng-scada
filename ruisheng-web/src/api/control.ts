import { apiClient } from '@/api/client'

export interface ControlRequest {
  action: string
  fun_code?: number
  reg?: number
  value?: number
  high_risk?: boolean
  params?: Record<string, unknown>
  otp?: string
}

export interface ControlAck {
  cmd_id: string
  status: 'pending' | 'duplicate'
}

function toControlPayload(req: ControlRequest): {
  fun_code: number
  reg: number
  value: number
  high_risk: boolean
} {
  if (req.fun_code !== undefined && req.reg !== undefined && req.value !== undefined) {
    return {
      fun_code: req.fun_code,
      reg: req.reg,
      value: req.value,
      high_risk: req.high_risk ?? false,
    }
  }
  const presets: Record<string, { fun_code: number; reg: number; value: number }> = {
    start: { fun_code: 6, reg: 0, value: 1 },
    stop: { fun_code: 6, reg: 0, value: 0 },
    reset: { fun_code: 6, reg: 1, value: 1 },
    set_speed: { fun_code: 6, reg: 2, value: Number(req.params?.speed ?? 0) },
  }
  const preset = presets[req.action] ?? { fun_code: 6, reg: 0, value: 1 }
  return {
    fun_code: preset.fun_code,
    reg: preset.reg,
    value: preset.value,
    high_risk: req.high_risk ?? false,
  }
}

export async function sendControl(devNumber: string, req: ControlRequest): Promise<ControlAck> {
  const headers: Record<string, string> = {}
  if (req.otp) headers['X-OTP-Code'] = req.otp
  const { data } = await apiClient.post(`/devices/${devNumber}/control`, toControlPayload(req), {
    headers,
  })
  return data.data as ControlAck
}

export async function cancelCommand(cmdId: string): Promise<void> {
  await apiClient.delete(`/control/commands/${cmdId}`)
}

import { apiClient } from '@/api/client'

export interface ControlRequest {
  action: string
  params?: Record<string, unknown>
  otp?: string
}

export interface ControlAck {
  cmd_id: string
  status: 'pending'
}

export async function sendControl(devNumber: string, req: ControlRequest): Promise<ControlAck> {
  const headers: Record<string, string> = {}
  if (req.otp) headers['X-OTP-Code'] = req.otp
  const { data } = await apiClient.post(`/devices/${devNumber}/control`, req, { headers })
  return data.data as ControlAck
}

export async function cancelCommand(cmdId: string): Promise<void> {
  await apiClient.delete(`/control/commands/${cmdId}`)
}

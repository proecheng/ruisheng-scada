export interface ApiErrorResponse {
  code: number
  message: string
  trace_id?: string
  data?: unknown
}

export interface FriendlyMessage {
  headline: string
  hint?: string
}

const ERR_MAP: Record<number, FriendlyMessage> = {
  [-100]: { headline: '参数错误', hint: '请检查输入后重试' },
  [-101]: { headline: '登录已过期', hint: '请重新登录' },
  [-102]: { headline: '权限不足', hint: '请联系管理员申请权限' },
  [-200]: { headline: '设备离线', hint: '可能 DTU 断网，稍后再试或查看信号' },
  [-201]: { headline: '设备忙', hint: '上一条命令尚未完成，请稍后重试' },
  [-300]: { headline: '服务繁忙', hint: '请稍后再试' },
  [-400]: { headline: '重复提交', hint: '该操作已在进行中' },
}

export function mapErrCode(code: number, rawMessage: string): FriendlyMessage {
  if (code === 0) return { headline: rawMessage }
  if (code === -100 && rawMessage === 'serial_port and modbus_addr already in use') {
    return { headline: '该串口的从站地址已被占用', hint: '停用设备仍占用地址' }
  }
  if (code === -100 && rawMessage === 'zero_origin_38 points require FC3 and register span within 0..37') {
    return { headline: '点位不符合38寄存器读取方案', hint: '功能码须为03，完整寄存器范围须在0至37内' }
  }
  return ERR_MAP[code] ?? { headline: rawMessage }
}

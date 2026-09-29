import { describe, it, expect } from 'vitest'
import { mapErrCode, type ApiErrorResponse } from '@/utils/errors'

describe('mapErrCode', () => {
  it('explains incompatible points in the fixed serial profile', () => {
    expect(mapErrCode(-100, 'zero_origin_38 points require FC3 and register span within 0..37')).toEqual({
      headline: '点位不符合38寄存器读取方案',
      hint: '功能码须为03，完整寄存器范围须在0至37内',
    })
  })

  it('identifies occupied serial addresses without replacing other validation messages', () => {
    expect(mapErrCode(-100, 'serial_port and modbus_addr already in use').headline).toBe('该串口的从站地址已被占用')
    expect(mapErrCode(-100, 'other validation error').headline).toBe('参数错误')
  })

  it('maps -200 to device offline with suggestion', () => {
    const msg = mapErrCode(-200, '设备离线')
    expect(msg.headline).toContain('设备离线')
    expect(msg.hint).toBeTruthy()
  })

  it('maps unknown negative codes to generic + raw message', () => {
    const msg = mapErrCode(-99999, 'weird')
    expect(msg.headline).toBe('weird')
  })

  it('maps 0 as success (no headline)', () => {
    const msg = mapErrCode(0, 'ok')
    expect(msg.headline).toBe('ok')
  })

  it('extracts error from axios response shape', () => {
    const payload: ApiErrorResponse = { code: -200, message: 'offline', trace_id: 'trace-x' }
    const msg = mapErrCode(payload.code, payload.message)
    expect(msg.headline).toBeTruthy()
  })
})

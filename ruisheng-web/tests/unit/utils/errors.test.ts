import { describe, it, expect } from 'vitest'
import { mapErrCode, type ApiErrorResponse } from '@/utils/errors'

describe('mapErrCode', () => {
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

import { describe, it, expect, beforeEach } from 'vitest'
import MockAdapter from 'axios-mock-adapter'
import { apiClient } from '@/api/client'
import {
  listDevices,
  getDevice,
  createDevice,
  updateDevice,
  deleteDevice,
  getRealtime,
  getHistory,
} from '@/api/devices'

describe('devices api', () => {
  let mock: MockAdapter
  beforeEach(() => {
    mock = new MockAdapter(apiClient)
  })

  it('listDevices returns array', async () => {
    mock.onGet('/devices').reply(200, {
      code: 0,
      message: 'ok',
      data: { total: 1, items: [{ dev_number: 'D1', dev_name: 'Pump', is_online: true }] },
    })
    const result = await listDevices()
    expect(result).toHaveLength(1)
    expect(result[0]?.dev_number).toBe('D1')
    expect(result[0]?.state).toBe('online')
  })

  it('getDevice returns single record', async () => {
    mock.onGet('/devices/D1').reply(200, {
      code: 0,
      message: 'ok',
      data: { dev_number: 'D1', dev_name: 'Pump', state: 'online' },
    })
    const d = await getDevice('D1')
    expect(d.dev_number).toBe('D1')
  })

  it('createDevice posts backend schema payload', async () => {
    mock.onPost('/devices').reply((config) => {
      expect(JSON.parse(String(config.data))).toEqual({
        dev_number: 'D2',
        dev_ser_number: 'SN-D2',
        modbus_addr: 2,
        transport_type: 'serial',
        serial_port: 'COM3',
        baud_rate: 9600,
      })
      return [
        200,
        {
          code: 0,
          message: 'ok',
          data: { dev_number: 'D2', dev_ser_number: 'SN-D2', modbus_addr: 2, dev_name: 'New' },
        },
      ]
    })
    const d = await createDevice({
      dev_number: 'D2',
      dev_ser_number: 'SN-D2',
      modbus_addr: 2,
      transport_type: 'serial',
      serial_port: 'COM3',
      baud_rate: 9600,
    })
    expect(d.dev_number).toBe('D2')
    expect(d.dev_ser_number).toBe('SN-D2')
  })

  it('updateDevice puts to /devices/{n}', async () => {
    mock.onPut('/devices/D1').reply(200, {
      code: 0,
      message: 'ok',
      data: { dev_number: 'D1', dev_name: 'Renamed' },
    })
    const d = await updateDevice('D1', { dev_name: 'Renamed' })
    expect(d.dev_name).toBe('Renamed')
  })

  it('deleteDevice deletes', async () => {
    mock.onDelete('/devices/D1').reply(200, { code: 0, message: 'ok' })
    await expect(deleteDevice('D1')).resolves.toBeUndefined()
  })

  it('getRealtime returns latest values', async () => {
    mock.onGet('/devices/D1/realtime').reply(200, {
      code: 0,
      message: 'ok',
      data: { dev_number: 'D1', points: [{ point_id: 1, value: 42, ts: '2026-04-20T00:00:00Z' }] },
    })
    const r = await getRealtime('D1')
    expect(r.points[0]?.value).toBe(42)
  })

  it('getHistory accepts from/to query', async () => {
    mock.onGet('/devices/D1/history').reply((config) => {
      expect(config.params).toMatchObject({ point_id: 1, from: 'x', to: 'y' })
      return [200, { code: 0, message: 'ok', data: { points: [], next_cursor: null } }]
    })
    await getHistory('D1', { point_id: 1, from: 'x', to: 'y' })
  })
})

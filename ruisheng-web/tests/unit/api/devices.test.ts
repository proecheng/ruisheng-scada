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
    expect(result[0]?.read_profile).toBe('point_groups')
    expect(mock.history.get[0]?.params).toEqual({ offset: 0, limit: 500 })
  })

  it('loads every envelope page with filters and preserves serial profiles', async () => {
    const rows = Array.from({ length: 128 }, (_, i) => ({
      dev_number: `D${i + 1}`,
      transport_type: 'serial',
      serial_port: 'COM3',
      modbus_addr: i + 1,
      read_profile: 'zero_origin_38',
    }))
    mock.onGet('/devices').reply((config) => {
      const offset = Number(config.params.offset)
      expect(config.params).toMatchObject({ limit: 500, q: 'D', company: 'demo' })
      return [200, { code: 0, data: { total: rows.length, items: rows.slice(offset, offset + 50) } }]
    })
    const result = await listDevices({ q: 'D', company: 'demo' })
    expect(result).toHaveLength(128)
    expect(result[127]).toMatchObject({ dev_number: 'D128', modbus_addr: 128, read_profile: 'zero_origin_38' })
    expect(mock.history.get.map((request) => request.params.offset)).toEqual([0, 50, 100])
  })

  it('retains flat-array compatibility without requesting more pages', async () => {
    mock.onGet('/devices').reply(200, { code: 0, data: [{ dev_number: 'D1' }] })
    expect(await listDevices()).toMatchObject([{ dev_number: 'D1', read_profile: 'point_groups' }])
    expect(mock.history.get).toHaveLength(1)
  })

  it('stops on an empty page when total changes during loading', async () => {
    mock.onGet('/devices').replyOnce(200, { code: 0, data: { total: 2, items: [{ dev_number: 'D1' }] } })
    mock.onGet('/devices').reply(200, { code: 0, data: { total: 2, items: [] } })
    expect(await listDevices()).toHaveLength(1)
    expect(mock.history.get).toHaveLength(2)
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
        read_profile: 'zero_origin_38',
        serial_port: 'COM3',
        baud_rate: 9600,
      })
      return [
        200,
        {
          code: 0,
          message: 'ok',
          data: { dev_number: 'D2', dev_ser_number: 'SN-D2', modbus_addr: 2, dev_name: 'New', read_profile: 'zero_origin_38' },
        },
      ]
    })
    const d = await createDevice({
      dev_number: 'D2',
      dev_ser_number: 'SN-D2',
      modbus_addr: 2,
      transport_type: 'serial',
      read_profile: 'zero_origin_38',
      serial_port: 'COM3',
      baud_rate: 9600,
    })
    expect(d.dev_number).toBe('D2')
    expect(d.dev_ser_number).toBe('SN-D2')
    expect(d.read_profile).toBe('zero_origin_38')
  })

  it('sends an explicit default profile when switching a serial device to TCP', async () => {
    mock.onPut('/devices/D1').reply((config) => {
      expect(JSON.parse(String(config.data))).toEqual({ transport_type: 'tcp', serial_port: null, read_profile: 'point_groups' })
      return [200, { code: 0, data: { dev_number: 'D1', transport_type: 'tcp', read_profile: 'point_groups' } }]
    })
    expect(await updateDevice('D1', { transport_type: 'tcp', serial_port: null, read_profile: 'point_groups' }))
      .toMatchObject({ transport_type: 'tcp', read_profile: 'point_groups' })
  })

  it('reports a serial address conflict as a useful localized error', async () => {
    mock.onPut('/devices/D1').reply(400, { code: -100, message: 'serial_port and modbus_addr already in use' })
    await expect(updateDevice('D1', { modbus_addr: 2 })).rejects.toThrow('该串口的从站地址已被占用')
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


  it('preserves null and zero samples without fabricating values or timestamps', async () => {
    mock.onGet('/devices/D1/realtime').reply(200, { code: 0, data: {
      dev_number: 'D1', points: [
        { point_id: 1, rt_value: null, recorded_at: '2026-09-15T03:20:00Z' },
        { point_id: 2, rt_value: 0, recorded_at: '2026-09-15T03:20:00Z' },
        { point_id: 3 },
      ],
    } })
    const result = await getRealtime('D1')
    expect(result.points).toMatchObject([
      { point_id: 1, value: null, ts: '2026-09-15T03:20:00Z' },
      { point_id: 2, value: 0, ts: '2026-09-15T03:20:00Z' },
      { point_id: 3, value: null, ts: '' },
    ])
  })

  it('getHistory accepts from/to query', async () => {
    mock.onGet('/devices/D1/history').reply((config) => {
      expect(config.params).toMatchObject({ point_id: 1, from: 'x', to: 'y' })
      return [200, { code: 0, message: 'ok', data: { points: [], next_cursor: null } }]
    })
    await getHistory('D1', { point_id: 1, from: 'x', to: 'y' })
  })
})

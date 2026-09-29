import type { Page } from '@playwright/test'
import type { Device, DeviceCreatePayload, DeviceUpdatePayload } from '../../src/api/devices'

export async function mockSerialDevices(page: Page, count = 5) {
  const devices: Device[] = Array.from({ length: count }, (_, index) => ({
    dev_number: `RS${String(index + 1).padStart(3, '0')}`,
    dev_ser_number: `SN-RS${index + 1}`,
    dev_name: `${index + 1}号采集设备`,
    state: 'offline',
    is_online: false,
    is_enabled: true,
    transport_type: 'serial',
    serial_port: 'COM3',
    modbus_addr: index + 1,
    read_profile: 'zero_origin_38',
    update_interval_decisec: 100,
    baud_rate: 9600,
  }))
  const writes: Array<{ method: string; path: string; body?: DeviceCreatePayload | DeviceUpdatePayload }> = []
  await page.route((url) => url.pathname.startsWith('/api/'), async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const method = request.method()
    const ok = (data: unknown) => route.fulfill({ json: { code: 0, message: 'ok', data } })
    const fail = (message: string) => route.fulfill({ status: 400, json: { code: -100, message } })
    if (path === '/api/device-templates') return ok({ items: [] })
    if (path === '/api/devices' && method === 'GET') {
      const offset = Number(url.searchParams.get('offset') ?? 0)
      const limit = Number(url.searchParams.get('limit') ?? 50)
      return ok({ total: devices.length, items: devices.slice(offset, offset + limit) })
    }
    const match = path.match(/^\/api\/devices\/([^/]+)(?:\/(enabled|realtime))?$/)
    const device = devices.find((item) => item.dev_number === match?.[1])
    if (match && method === 'GET') {
      if (match[2] === 'realtime') return ok({ dev_number: match[1], points: [] })
      return device ? ok(device) : fail('device not found')
    }
    if (method === 'DELETE' && device) {
      writes.push({ method, path })
      devices.splice(devices.indexOf(device), 1)
      return ok({ deleted: device.dev_number })
    }
    if ((path === '/api/devices' && method === 'POST') || (device && method === 'PUT')) {
      const body = request.postDataJSON() as DeviceCreatePayload | DeviceUpdatePayload
      writes.push({ method, path, body })
      const next = { ...device, ...body }
      if (next.transport_type === 'tcp' && next.read_profile !== 'point_groups') return fail('zero_origin_38 requires serial transport')
      if (next.transport_type === 'serial' && devices.some((item) =>
        item !== device && item.transport_type === 'serial' && item.serial_port === next.serial_port && item.modbus_addr === next.modbus_addr,
      )) return fail('serial_port and modbus_addr already in use')
      if (device) {
        Object.assign(device, body)
        if (!device.is_enabled) Object.assign(device, { is_online: false, state: 'offline' })
        return ok(device)
      }
      const create = body as DeviceCreatePayload
      const created: Device = {
        ...create,
        dev_name: create.dev_name ?? create.dev_number,
        state: 'offline',
        is_enabled: true,
        read_profile: create.read_profile ?? 'point_groups',
      }
      devices.push(created)
      return ok(created)
    }
    return route.fulfill({ status: 404, json: { code: -100, message: `Unmocked request: ${method} ${path}` } })
  })
  return { devices, writes }
}

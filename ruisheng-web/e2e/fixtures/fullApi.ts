import type { Page, Route } from '@playwright/test'
import { MOCK_DEVICES, MOCK_SESSION } from './auth'

const now = new Date('2026-04-29T09:00:00.000Z').toISOString()

const device = {
  ...MOCK_DEVICES[0],
  id: 1,
  dev_ser_number: 'SN-DEV001',
  is_online: true,
  is_enabled: true,
  transport_type: 'tcp',
  serial_port: null,
  dev_ip: '192.168.1.20',
  modbus_addr: 1,
  baud_rate: 9600,
  update_interval_decisec: 100,
  loss_count: 0,
  usr_group: 'demo',
}

const points = [
  {
    id: 11,
    dev_number: 'DEV001',
    point_name: 'temperature',
    user_point_name: '温度',
    point_number: 1,
    fun_code: 3,
    dev_addr: 1,
    r_bit: null,
    value_type: '字',
    point_unit: '℃',
    point_ratio: 1,
    point_offset: 0,
    user_ratio: 1,
    user_point_offset: 0,
    min_value: null,
    max_value: 80,
    show: 1,
  },
  {
    id: 12,
    dev_number: 'DEV001',
    point_name: 'pressure',
    user_point_name: '压力',
    point_number: 2,
    fun_code: 4,
    dev_addr: 1,
    r_bit: null,
    value_type: '双字',
    point_unit: 'MPa',
    point_ratio: 1,
    point_offset: 0,
    user_ratio: 1,
    user_point_offset: 0,
    min_value: null,
    max_value: 1.6,
    show: 1,
  },
]

const templates = [
  {
    id: 101,
    name: '泵站模板',
    dev_type: 'pump',
    payload: {
      points: [
        {
          point_name: 'flow',
          user_point_name: '流量',
          point_number: 12,
          fun_code: 4,
          dev_addr: 1,
          value_type: '双字',
          point_ratio: 1,
          point_offset: 0,
          user_ratio: 1,
          user_point_offset: 0,
          show: 1,
        },
      ],
    },
  },
]

const alarmConfigs = [
  {
    id: 21,
    dev_number: 'DEV001',
    point_id: 11,
    alarm_name: '温度上限',
    alarm_type: '>',
    limit_value: 80,
    enable: true,
    phone_alarm: 1,
    reset_remind: false,
    waring_flag: false,
  },
]

const alarmRecords = [
  {
    id: 31,
    dev_number: 'DEV001',
    point_id: 11,
    alarm_name: '温度上限',
    alarm_msg: '温度过高',
    alarm_value: 88,
    channels_sent: {},
    triggered_at: now,
    reset_at: null,
  },
]

const users = [
  {
    user_name: '13800138000',
    display_name: '管理员',
    authority: 'Administrators',
    usr_group: 'demo',
    company: '润盛公司',
    department: '运维部',
    control_authority: 7,
  },
]

const phones = [{ id: 41, phone_number: '13800138000' }]
const emails = [{ id: 51, phone_number: '13800138000', email: 'ops@example.com' }]

function ok(route: Route, data: unknown): Promise<void> {
  return route.fulfill({ json: { code: 0, msg: 'ok', message: 'ok', data } })
}

function createdId(prefix: number): number {
  return prefix + Math.floor(Math.random() * 1000)
}

export async function mockFullApi(page: Page): Promise<void> {
  const createdDevices: Record<string, Record<string, unknown>> = {}

  await page.route(
    (url) => url.pathname.startsWith('/api/'),
    async (route) => {
      const req = route.request()
      const url = new URL(req.url())
      const path = url.pathname
      const method = req.method()

      if (path === '/api/auth/logout') return ok(route, { logout: true })
      if (path === '/api/auth/otp/send') return ok(route, { sent: true, ttl_sec: 300 })

      if (path === '/api/devices' && method === 'GET') {
        const items = [...MOCK_DEVICES, ...Object.values(createdDevices)]
        return ok(route, { total: items.length, items })
      }
      if (path === '/api/devices' && method === 'POST') {
        const body = req.postDataJSON()
        const devNumber = String(body.dev_number)
        const created = {
          id: createdId(10),
          is_online: false,
          is_enabled: true,
          state: 'offline',
          loss_count: 0,
          update_interval_decisec: body.update_interval_decisec ?? 100,
          usr_group: 'demo',
          ...body,
        }
        createdDevices[devNumber] = created
        return ok(route, created)
      }
      if (path === '/api/devices/DEV001' && method === 'GET') return ok(route, device)
      if (path === '/api/devices/DEV001' && method === 'PUT') {
        Object.assign(device, req.postDataJSON())
        return ok(route, device)
      }
      if (path === '/api/devices/DEV001/enabled' && method === 'PUT') {
        Object.assign(device, req.postDataJSON(), { is_online: req.postDataJSON().is_enabled ? device.is_online : false })
        return ok(route, device)
      }
      const createdDeviceMatch = path.match(/^\/api\/devices\/([^/]+)$/)
      if (createdDeviceMatch && method === 'GET') {
        const devNumber = decodeURIComponent(createdDeviceMatch[1] ?? '')
        if (createdDevices[devNumber]) return ok(route, createdDevices[devNumber])
      }
      if (path === '/api/devices/DEV001/realtime') {
        return ok(route, {
          dev_number: 'DEV001',
          points: [{ point_id: 11, point_name: '温度', value: 26.5, ts: now, unit: '℃' }],
        })
      }
      const realtimeMatch = path.match(/^\/api\/devices\/([^/]+)\/realtime$/)
      if (realtimeMatch) {
        return ok(route, { dev_number: decodeURIComponent(realtimeMatch[1] ?? ''), points: [] })
      }
      if (path === '/api/devices/DEV001/history') {
        return ok(route, {
          rows: [
            { point_id: 11, recorded_at: '2026-04-29T08:00:00Z', rt_value: 25.1 },
            { point_id: 12, recorded_at: '2026-04-29T08:00:00Z', rt_value: 0.42 },
            { point_id: 11, recorded_at: '2026-04-29T09:00:00Z', rt_value: 26.5 },
            { point_id: 12, recorded_at: '2026-04-29T09:00:00Z', rt_value: 0.45 },
          ],
          next_offset: null,
        })
      }
      if (path === '/api/control/actions') {
        return ok(route, {
          items: [
            { key: 'start', label: '启动', fun_code: 6, reg: 0, value: 1, high_risk: false, description: '寄存器 0 = 1' },
            { key: 'stop', label: '停止', fun_code: 6, reg: 0, value: 0, high_risk: true, description: '寄存器 0 = 0' },
          ],
        })
      }
      if (path === '/api/devices/DEV001/control' && method === 'POST') {
        return ok(route, { cmd_id: '01KQC8E2E2TESTCMD00000000', status: 'pending' })
      }
      if (path.startsWith('/api/control/commands/') && method === 'DELETE') {
        return ok(route, { status: 'cancelled' })
      }

      if (path === '/api/devices/DEV001/points' && method === 'GET') {
        return ok(route, { items: points })
      }
      if (path === '/api/devices/DEV001/points/export') {
        return route.fulfill({
          body: 'point_name,user_point_name,point_number,fun_code,dev_addr,value_type\nflow,流量,12,4,1,双字\n',
          headers: { 'content-type': 'text/csv' },
        })
      }
      if (path === '/api/devices/DEV001/points/import' && method === 'POST') {
        return ok(route, { imported: 1, items: [{ ...points[0], id: createdId(300), user_point_name: '导入点位' }] })
      }
      if (path === '/api/devices/DEV001/points' && method === 'POST') {
        const body = req.postDataJSON()
        return ok(route, { ...points[0], id: createdId(100), ...body })
      }
      if (path.startsWith('/api/devices/DEV001/points/') && method === 'PUT') {
        const body = req.postDataJSON()
        return ok(route, { ...points[0], ...body })
      }
      if (path.startsWith('/api/devices/DEV001/points/') && method === 'DELETE') {
        return ok(route, { deleted: 11 })
      }

      if (path === '/api/device-templates' && method === 'GET') return ok(route, { items: templates })
      if (path === '/api/device-templates' && method === 'POST') {
        const created = { id: createdId(1000), ...req.postDataJSON() }
        templates.push(created)
        return ok(route, created)
      }
      if (path.startsWith('/api/device-templates/') && method === 'PUT') {
        return ok(route, { id: Number(path.split('/').pop()), ...req.postDataJSON() })
      }
      if (path.startsWith('/api/device-templates/') && method === 'DELETE') {
        return ok(route, { deleted: Number(path.split('/').pop()) })
      }

      if (path === '/api/devices/DEV001/alarms/configs' && method === 'GET') {
        return ok(route, { items: alarmConfigs })
      }
      if (path === '/api/devices/DEV001/alarms/configs' && method === 'POST') {
        const body = req.postDataJSON()
        return ok(route, { ...alarmConfigs[0], id: createdId(200), ...body })
      }
      if (path.startsWith('/api/devices/DEV001/alarms/configs/') && method === 'PUT') {
        const body = req.postDataJSON()
        return ok(route, { ...alarmConfigs[0], ...body })
      }
      if (path.startsWith('/api/devices/DEV001/alarms/configs/') && method === 'DELETE') {
        return ok(route, { deleted: 21 })
      }
      if (path === '/api/alarms' && method === 'GET') {
        return ok(route, { items: alarmRecords, next_cursor: null })
      }
      if (path.startsWith('/api/alarms/') && method === 'PUT') {
        return ok(route, { reset: true })
      }

      if (path === '/api/reports/daily') {
        return ok(route, { DEV001: { 11: { count: 2, min: 25.1, max: 26.5, avg: 25.8 } } })
      }
      if (path === '/api/waveforms/DEV001/11') {
        return ok(route, { ts: now, samples: [0, 1, 0, -1], sample_rate_hz: 100 })
      }
      if (path === '/api/waveforms/analyze') {
        return ok(route, { freqs: [50, 100], magnitudes: [1.2, 0.4] })
      }

      if (path === '/api/plans/timing' && method === 'GET') {
        return ok(route, {
          items: [
            {
              id: 61,
              dev_number: 'DEV001',
              action_at: now,
              action: 1,
              repetition: 0,
              enable: true,
            },
          ],
        })
      }
      if (path === '/api/plans/timing' && method === 'POST') {
        return ok(route, { id: createdId(600), ...req.postDataJSON(), enable: true })
      }
      if (path.startsWith('/api/plans/timing/') && method === 'PUT') {
        return ok(route, { id: 61, dev_number: 'DEV001', action: 2, action_at: now, enable: false })
      }
      if (path.startsWith('/api/plans/timing/') && method === 'DELETE') {
        return ok(route, { deleted: 61 })
      }

      if (path === '/api/plans/maintenance' && method === 'GET') {
        return ok(route, {
          items: [
            {
              id: 71,
              dev_number: 'DEV001',
              plan_name: '月度保养',
              interval_days: 30,
              next_due_at: '2026-05-29T00:00:00Z',
              enable: true,
            },
          ],
        })
      }
      if (path === '/api/plans/maintenance' && method === 'POST') {
        return ok(route, { id: createdId(700), ...req.postDataJSON(), enable: true })
      }
      if (path === '/api/plans/maintenance/71' && method === 'PUT') {
        return ok(route, { id: 71, ...req.postDataJSON(), enable: true })
      }
      if (path === '/api/plans/maintenance/71/complete') {
        return ok(route, { status: 'completed' })
      }
      if (path === '/api/plans/maintenance/71' && method === 'DELETE') {
        return ok(route, { deleted: 71 })
      }

      if (path === '/api/scenes/pages' && method === 'GET') {
        return ok(route, {
          items: [{ id: 81, page_name: '主画面', pos_x: 0, pos_y: 0, radius: 20 }],
        })
      }
      if (path === '/api/scenes/pages' && method === 'POST') {
        return ok(route, { id: createdId(800), ...req.postDataJSON() })
      }
      if (path === '/api/scenes/pages/81/views' && method === 'GET') {
        return ok(route, {
          items: [{ id: 91, scene_page_id: 81, dev_number: 'DEV001', pos_x: 120, pos_y: 140, radius: 24 }],
        })
      }
      if (path === '/api/scenes/pages/81/views' && method === 'POST') {
        return ok(route, { id: createdId(900), scene_page_id: 81, ...req.postDataJSON() })
      }
      if (path === '/api/scenes/pages/81/views/91' && method === 'DELETE') {
        return ok(route, { deleted: 91 })
      }

      if (path === '/api/orgs/users') return ok(route, { total: users.length, items: users })
      if (path.startsWith('/api/orgs/users/') && path.endsWith('/phones')) {
        if (method === 'GET') return ok(route, { items: phones })
        if (method === 'POST') return ok(route, { id: createdId(400), ...req.postDataJSON() })
      }
      if (path.includes('/phones/') && method === 'DELETE') return ok(route, { deleted: true })
      if (path.startsWith('/api/orgs/users/') && path.endsWith('/emails')) {
        if (method === 'GET') return ok(route, { items: emails })
        if (method === 'POST') return ok(route, { id: createdId(500), ...req.postDataJSON() })
      }
      if (path.includes('/emails/') && method === 'DELETE') return ok(route, { deleted: true })

      if (path === '/api/pay/orders' && method === 'GET') {
        return ok(route, {
          items: [
            {
              out_trade_no: 'ORDER001',
              openid: 'DEV001',
              total_fee: 1000,
              body: '设备充值',
              pay_state: 'pending',
              created_at: now,
            },
          ],
        })
      }
      if (path === '/api/pay/orders' && method === 'POST') {
        return ok(route, {
          out_trade_no: 'ORDER002',
          openid: req.postDataJSON().openid,
          total_fee: req.postDataJSON().amount_fen,
          body: req.postDataJSON().description,
          pay_state: 'pending',
          code_url: 'weixin://pay/test',
          created_at: now,
        })
      }
      if (path === '/api/pay/orders/ORDER002') {
        return ok(route, {
          out_trade_no: 'ORDER002',
          openid: 'DEV001',
          total_fee: 1000,
          body: '设备充值',
          pay_state: 'paid',
          created_at: now,
        })
      }

      if (path === '/api/meta/version') {
        return ok(route, { api_version: '0.1.0', build_hash: 'test', build_time: now, db_schema_version: 'test' })
      }
      if (path === '/api/health/ready') {
        return ok(route, { ok: true, components: { db: { ok: true }, redis: { ok: true } } })
      }

      return ok(route, {})
    },
  )

  await page.route((url) => url.pathname === '/ws', (route) => route.abort())
}

export { MOCK_SESSION }

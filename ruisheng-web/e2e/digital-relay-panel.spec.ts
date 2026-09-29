import { test, expect } from '@playwright/test'
import { injectAuthState } from './fixtures/auth'
import { mockFullApi } from './fixtures/fullApi'

for (const [name, width, height] of [['desktop', 1440, 1000], ['mobile', 390, 900]] as const) {
  test(`realtime ${name}: four channel states, direct buttons and verified execution`, async ({ page }) => {
    await page.setViewportSize({ width, height })
    await mockFullApi(page)
    await page.routeWebSocket('**/ws**', ws => ws.onMessage(() => {}))
    let input = 3
    let output = 0
    let confirmed = false
    const posts: unknown[] = []
    await page.route('**/api/devices/DEV001', route => route.fulfill({ json: { code: 0, data: { dev_number: 'DEV001', dev_name: '1号泵站', transport_type: 'serial', read_profile: 'zero_origin_38', modbus_addr: 1, is_enabled: true, is_online: true, update_interval_decisec: 50 } } }))
    await page.route('**/api/devices/DEV001/points', route => route.fulfill({ json: { code: 0, data: { items: [0, 1].map((point_number, i) => ({ id: [71, 92][i], point_name: 'custom', point_number, fun_code: 3, dev_addr: 1, value_type: '字', display_bits: 2, point_ratio: 1, point_offset: 0, user_ratio: 1, user_point_offset: 0 })) } } }))
    await page.route('**/api/devices/DEV001/realtime', route => route.fulfill({ json: { code: 0, data: { dev_number: 'DEV001', points: [
      { point_id: 71, point_name: '开关量输入', rt_value: input, recorded_at: new Date().toISOString(), display_bits: 2 },
      { point_id: 92, point_name: '开关量输出', rt_value: output, recorded_at: new Date().toISOString(), display_bits: 2 },
      { point_id: 30, point_name: '温度', rt_value: 29, point_unit: '°C', recorded_at: new Date().toISOString() },
    ] } } }))
    await page.route('**/api/devices/DEV001/do-control', route => {
      if (route.request().method() === 'POST') {
        posts.push(route.request().postDataJSON())
        return route.fulfill({ json: { code: 0, data: { cmd_id: 'relay-test' } } })
      }
      return route.fulfill({ json: { code: 0, data: { supported: true, enabled: true, channels: [1, 2], config_version: 27, sample: { org_value: output, recorded_at: new Date().toISOString() } } } })
    })
    await page.route('**/api/devices/DEV001/do-commands/relay-test', route => route.fulfill({ json: { code: 0, data: {
      cmd_id: 'relay-test', result: confirmed ? 'success' : 'pending',
      execution: confirmed ? { readback: 1, channels: [{ channel: 1, high: true, phase: 'verified' }] } : null,
    } } }))
    await injectAuthState(page)
    await page.goto('/devices/DEV001')
    await expect(page.getByRole('heading', { name: '输入 / 输出' })).toBeVisible()
    await expect(page.getByLabel('DI1输入状态')).toContainText('高电平 1')
    await expect(page.getByLabel('DI2输入状态')).toContainText('高电平 1')
    await expect(page.getByLabel('开关1输出控制')).toContainText('已断开 0')
    await expect(page.getByLabel('开关2输出控制')).toContainText('已断开 0')
    await expect(page.getByRole('button', { name: '闭合开关1' })).toBeEnabled()
    await page.screenshot({ path: `D:/江苏润盛/tmp-test-logs/relay-debug-20260921/panel-${name}.png`, fullPage: true })
    await page.getByRole('button', { name: '闭合开关1' }).click()
    await expect(page.getByRole('dialog')).toContainText('开关1（DO1）→ 闭合（1）')
    expect(posts).toHaveLength(0)
    await expect(page.locator('.type-to-confirm input')).toHaveCount(0)
    await page.getByRole('button', { name: '确认下发', exact: true }).click()
    await expect(page.locator('.execution-status')).toContainText('等待逐路回读确认')
    await expect(page.getByLabel('开关1输出控制')).toContainText('已断开 0')
    await expect(page.getByRole('button', { name: '闭合开关2' })).toBeDisabled()
    expect(posts).toEqual([{ channels: [{ number: 1, high: true }], config_version: 27, high_risk: false }])
    output = 1; input = 2; confirmed = true
    await expect(page.locator('.execution-status')).toContainText('执行成功，设备回读已确认')
    await expect(page.getByLabel('开关1输出控制')).toContainText('已闭合 1')
    await expect(page.getByLabel('开关2输出控制')).toContainText('已断开 0')
    await expect(page.getByLabel('DI1输入状态')).toContainText('低电平 0', { timeout: 10000 })
    await expect(page.getByLabel('DI2输入状态')).toContainText('高电平 1')
    expect(posts).toHaveLength(1)
  })
}

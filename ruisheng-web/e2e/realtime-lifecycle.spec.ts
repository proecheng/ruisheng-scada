import { test, expect, type WebSocketRoute } from '@playwright/test'
import { injectAuthState } from './fixtures/auth'
import { mockFullApi } from './fixtures/fullApi'

test('38 live values survive repeated pushes, a slow failed refresh and navigation', async ({ page }) => {
  await mockFullApi(page)
  let socket: WebSocketRoute | undefined
  await page.routeWebSocket('**/ws**', ws => { socket = ws; ws.onMessage(() => {}) })
  let round = 0
  let fail = false
  let requests = 0
  const origin = Date.parse('2026-09-15T03:20:00Z')
  const stamp = () => new Date(origin + round * 5000).toISOString()
  const samples = () => Array.from({ length: 38 }, (_, i) => ({ point_id: i + 1,
    point_name: `寄存器${String(i).padStart(2, '0')}`, rt_value: i === 0 ? 0 : 1000 + i + round, recorded_at: stamp() }))
  await page.route('**/api/devices/DEV001', route => route.fulfill({ json: { code: 0, data: {
    dev_number: 'DEV001', dev_name: '1号泵站', is_enabled: true, is_online: true,
    transport_type: 'serial', serial_port: '/dev/ruisheng-rs485', update_interval_decisec: 50,
  } } }))
  await page.route('**/api/devices/DEV001/realtime', async route => {
    requests++
    if (fail) { await new Promise(resolve => setTimeout(resolve, 400)); return route.fulfill({ status: 503, json: {} }) }
    return route.fulfill({ json: { code: 0, data: { dev_number: 'DEV001', points: samples() } } })
  })
  await injectAuthState(page)
  await page.clock.install({ time: new Date(origin) })
  await page.goto('/devices/DEV001')
  await expect(page.locator('.point-card')).toHaveCount(38)
  await expect(page.locator('.ws-status')).toHaveAttribute('data-state', 'open')
  for (round = 1; round <= 6; round++) {
    await page.clock.runFor(5000)
    for (let i = 0; i < 38; i++) socket!.send(JSON.stringify({ type: 'realtime', dev_number: 'DEV001',
      point_id: i + 1, value: i === 0 ? 0 : 1000 + i + round, ts: stamp() }))
    await expect(page.locator('.point-card').nth(20).locator('.p-value')).toHaveText(String(1020 + round))
    await expect(page.locator('.point-card').nth(37).locator('.p-value')).toHaveText(String(1037 + round))
    await expect(page.locator('.point-card')).toHaveCount(38)
    await expect(page.locator('main')).not.toContainText('NaN')
  }
  expect(requests).toBeGreaterThanOrEqual(6)
  const previous = await page.locator('.point-card').nth(20).locator('.p-value').textContent()
  socket!.send(JSON.stringify({ type: 'realtime', dev_number: 'DEV001', point_id: 21, value: 0, ts: '' }))
  fail = true
  await page.clock.runFor(5000)
  await expect(page.locator('.point-card')).toHaveCount(38)
  await expect(page.locator('.device-detail [role="status"]')).toContainText('保留上次数据')
  await expect(page.locator('.point-card').nth(20).locator('.p-value')).toHaveText(previous!)
  fail = false
  await page.clock.runFor(5000)
  await expect(page.locator('.device-detail [role="status"]')).toHaveCount(0)
  await page.locator('.point-card').nth(20).click()
  await expect(page).toHaveURL(/history\?point_id=21/)
  await page.goBack()
  await expect(page.locator('.point-card')).toHaveCount(38)
  const backValue = await page.locator('.point-card').nth(20).locator('.p-value').textContent()
  await expect(page.locator('.point-card').nth(20).locator('.p-value')).toHaveText(backValue!)
})

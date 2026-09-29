import { test, expect, type WebSocketRoute } from '@playwright/test'
import { injectAuthState } from './fixtures/auth'
import { mockFullApi } from './fixtures/fullApi'

test('old DI and DO samples are historical until a fresh board response arrives', async ({ page }) => {
  await mockFullApi(page)
  let socket: WebSocketRoute | undefined
  await page.routeWebSocket('**/ws**', ws => { socket = ws; ws.onMessage(() => {}) })
  const old = new Date(Date.now() - 60000).toISOString()
  await page.route('**/api/devices/DEV001/realtime', route => route.fulfill({ json: { code: 0, data: {
    dev_number: 'DEV001', points: [
      { point_id: 1, point_name: 'DI', rt_value: 3, recorded_at: old, display_bits: 2 },
      { point_id: 2, point_name: 'DO', rt_value: 0, recorded_at: old, display_bits: 2 },
    ],
  } } }))
  await injectAuthState(page)
  await page.goto('/devices/DEV001')
  await expect(page.locator('.point-card[data-stale="true"]')).toHaveCount(2)
  await expect(page.locator('.p-channels [data-high="true"]')).toHaveCount(0)
  await expect(page.locator('.point-card').first()).toContainText('上次第1路：高电平')
  await expect.poll(() => !!socket).toBe(true)
  for (const [point_id, value] of [[1, 0], [2, 3]] as const) {
    socket!.send(JSON.stringify({ type: 'realtime', dev_number: 'DEV001', point_id, value, ts: new Date().toISOString() }))
  }
  await expect(page.locator('.point-card[data-stale="true"]')).toHaveCount(0)
  await expect(page.locator('.p-value').first()).toHaveText('00')
  await expect(page.locator('.p-value').nth(1)).toHaveText('11')
  await expect(page.locator('.point-card').nth(1)).toContainText('第2路：高电平')
})

test('DI and DO preserve binary width through live pushes and page reload', async ({ page }) => {
  await mockFullApi(page)
  let socket: WebSocketRoute | undefined
  await page.routeWebSocket('**/ws**', ws => { socket = ws; ws.onMessage(() => {}) })
  let value = 3
  const widths = [2, 4, 16]
  const stamp = () => new Date().toISOString()
  await page.route('**/api/devices/DEV001/realtime', route => route.fulfill({ json: { code: 0, data: {
    dev_number: 'DEV001', points: widths.map((width, index) => ({
      point_id: index + 1, point_name: ['DI', 'DO', '16路DI'][index],
      rt_value: index === 2 ? 32768 : value, recorded_at: stamp(), display_bits: width,
    })),
  } } }))
  await injectAuthState(page)
  await page.goto('/devices/DEV001')
  const di = page.locator('.point-card').nth(0)
  const output = page.locator('.point-card').nth(1)
  await expect(di.locator('.p-value')).toHaveText('11')
  await expect(di).toContainText('第1路：高电平')
  await expect(di).toContainText('第2路：高电平')
  await expect(output.locator('.p-value')).toHaveText('0011')
  await expect(output).toContainText('第4路：低电平')
  await expect(page.locator('.point-card').nth(2).locator('.p-value')).toHaveText('1000000000000000')
  await expect(page.locator('.point-card').nth(2)).toContainText('第16路：高电平')
  for (value of [0, 1, 2, 3]) {
    socket!.send(JSON.stringify({ type: 'realtime', dev_number: 'DEV001', point_id: 1, value, ts: stamp() }))
    await expect(di.locator('.p-value')).toHaveText(value.toString(2).padStart(2, '0'))
  }
  value = 2
  await page.reload()
  await expect(di.locator('.p-value')).toHaveText('10')
  await expect(di).toContainText('第1路：低电平')
  await expect(di).toContainText('第2路：高电平')
})

test('point editor saves a configurable channel count independently of register decoding', async ({ page }) => {
  await mockFullApi(page)
  await page.routeWebSocket('**/ws**', ws => { ws.onMessage(() => {}) })
  let point = { id: 1, point_name: 'DI', user_point_name: 'DI', point_number: 0, fun_code: 3,
    dev_addr: 1, r_bit: 0 as number | null, value_type: 'bit', display_bits: null as number | null,
    point_ratio: 1, point_offset: 0, user_ratio: 1, user_point_offset: 0, show: 1 }
  await page.route(url => /^\/api\/devices\/DEV001\/points(?:\/1)?$/.test(url.pathname), async route => {
    if (route.request().method() === 'PUT') {
      point = { ...point, ...route.request().postDataJSON() }
      return route.fulfill({ json: { code: 0, data: point } })
    }
    return route.fulfill({ json: { code: 0, data: { items: [point] } } })
  })
  await injectAuthState(page)
  await page.goto('/devices/DEV001/points')
  await page.getByRole('button', { name: '编辑', exact: true }).click()
  await expect(page.locator('.drawer')).toContainText('bit只读取所选的一位')
  await page.getByLabel('数据类型').selectOption('字')
  await page.getByLabel('显示方式').selectOption('binary')
  await page.getByLabel('显示位数', { exact: true }).fill('8')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.locator('.drawer')).toHaveCount(0)
  expect(point).toMatchObject({ value_type: '字', r_bit: null, display_bits: 8, point_ratio: 1, user_ratio: 1 })
  await page.reload()
  await expect(page.locator('.point-table')).toContainText('二进制（8位）')
  await page.getByRole('button', { name: '编辑', exact: true }).click()
  await expect(page.getByLabel('显示位数', { exact: true })).toHaveValue('8')
  await page.getByLabel('显示方式').selectOption('decimal')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.locator('.drawer')).toHaveCount(0)
  expect(point.display_bits).toBeNull()
  expect(point.value_type).toBe('字')
})

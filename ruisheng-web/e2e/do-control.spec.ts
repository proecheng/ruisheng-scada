import { test, expect } from '@playwright/test'
import { injectAuthState } from './fixtures/auth'
import { mockFullApi } from './fixtures/fullApi'

test('serial control page sends selected channels and renders partial execution honestly', async ({ page }) => {
  await mockFullApi(page)
  await page.routeWebSocket('**/ws**', ws => ws.onMessage(() => {}))
  await page.route('**/api/devices/DEV001', route => route.fulfill({ json: { code: 0, data: { dev_number: 'DEV001', transport_type: 'serial', is_enabled: true, is_online: true } } }))
  const posts: unknown[] = []
  await page.route('**/api/devices/DEV001/do-control', route => {
    if (route.request().method() === 'POST') {
      posts.push(route.request().postDataJSON())
      return route.fulfill({ json: { code: 0, data: { cmd_id: 'do-test', status: 'pending' } } })
    }
    return route.fulfill({ json: { code: 0, data: { supported: true, enabled: true, channels: [1, 2], config_version: 26, sample: { org_value: 0, recorded_at: new Date().toISOString() } } } })
  })
  await page.route('**/api/devices/DEV001/do-commands/do-test', route => route.fulfill({ json: { code: 0, data: { cmd_id: 'do-test', result: 'timeout', execution: { channels: [{ channel: 1, high: true, phase: 'verified' }, { channel: 2, high: true, phase: 'sending' }] } } } }))
  await injectAuthState(page)
  await page.goto('/devices/DEV001/control')
  await expect(page.getByRole('heading', { name: '开关输出控制' })).toBeVisible()
  await page.getByText('同时设置两路', { exact: true }).click()
  await expect(page.getByRole('button', { name: '应用所选状态' })).toBeDisabled()
  await page.getByLabel('开关1目标状态').selectOption('high')
  await page.getByLabel('开关2目标状态').selectOption('high')
  await page.getByRole('button', { name: '应用所选状态' }).click()
  await expect(page.getByRole('dialog')).toContainText('开关1（DO1）→ 闭合（1）；开关2（DO2）→ 闭合（1）')
  await expect(page.locator('.type-to-confirm input')).toHaveCount(0)
  await page.locator('button.confirm').click()
  await expect(page.locator('.do-panel [role="status"]')).toContainText('未自动重发')
  expect(posts).toEqual([{ channels: [{ number: 1, high: true }, { number: 2, high: true }], config_version: 26, high_risk: false }])
  await expect(page.locator('.do-panel')).toContainText('开关1：闭合 — 回读已确认')
  await expect(page.locator('.do-panel')).toContainText('开关2：闭合 — 状态未确认')
  await page.screenshot({ path: 'D:/江苏润盛/tmp-test-logs/relay-debug-20260921/control-page.png', fullPage: true })
})

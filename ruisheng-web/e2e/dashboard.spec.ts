import { test, expect } from '@playwright/test'
import { DashboardPage } from './pages/DashboardPage'
import { mockDevices, injectAuthState, MOCK_DEVICES, MOCK_SESSION } from './fixtures/auth'

test.describe('仪表板', () => {
  test.beforeEach(async ({ page }) => {
    // 注入 localStorage 模拟已登录，无需经过登录流程
    await injectAuthState(page)
    await mockDevices(page)
    await page.goto('/dashboard')
    await expect(page).toHaveURL('/dashboard')
  })

  test('显示欢迎信息含用户名', async ({ page }) => {
    const dashboard = new DashboardPage(page)
    await expect(dashboard.welcomeUsername).toContainText(MOCK_SESSION.user.user_name)
  })

  test('统计卡片显示正确数量', async ({ page }) => {
    const dashboard = new DashboardPage(page)

    const total = MOCK_DEVICES.length
    const online = MOCK_DEVICES.filter((d) => d.state === 'online').length
    const offline = MOCK_DEVICES.filter((d) => d.state === 'offline').length
    const warning = MOCK_DEVICES.filter((d) => d.state === 'warning').length

    await expect(dashboard.statTotal).toContainText(String(total))
    await expect(dashboard.statOnline).toContainText(String(online))
    await expect(dashboard.statOffline).toContainText(String(offline))
    await expect(dashboard.statWarning).toContainText(String(warning))
  })

  test('点击导航可跳转到设备列表', async ({ page }) => {
    await page.getByRole('link', { name: /设备/ }).first().click()
    await expect(page).toHaveURL('/devices')
  })
})

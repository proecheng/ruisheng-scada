import { test, expect } from '@playwright/test'

test.describe('真实后端联调', () => {
  test.skip(!process.env.E2E_REAL_BACKEND, '需要本机 API/PostgreSQL/Redis 已启动')

  test('真实账号登录后可打开核心只读页面', async ({ page }) => {
    await page.goto('/login')
    await page.getByTestId('login-username').fill('13800138000')
    await page.getByTestId('login-password').fill('Admin@2026!')
    await page.getByTestId('login-submit').click()

    await expect(page).toHaveURL('/dashboard')
    await expect(page.getByTestId('welcome-username')).toContainText('13800138000')

    await page.goto('/devices')
    await expect(page.getByTestId('device-number').filter({ hasText: '60270012' })).toBeVisible()

    await page.goto('/devices/60270012/points')
    await expect(page.getByRole('heading', { name: '60270012 — 点位配置' })).toBeVisible()

    await page.goto('/alarms')
    await expect(page.getByRole('heading', { name: /告警列表/ })).toBeVisible()

    await page.goto('/plans/timing')
    await expect(page.getByRole('heading', { name: '定时计划' })).toBeVisible()

    await page.goto('/settings/users')
    await expect(page.getByRole('heading', { name: '用户管理' })).toBeVisible()

    await page.goto('/__diag')
    await expect(page.getByText('全部组件健康')).toBeVisible()
  })
})

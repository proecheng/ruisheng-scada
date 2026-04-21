import { test, expect } from '@playwright/test'
import { DeviceListPage } from './pages/DeviceListPage'
import { mockDevices, injectAuthState, MOCK_DEVICES } from './fixtures/auth'

test.describe('设备列表', () => {
  test.beforeEach(async ({ page }) => {
    await injectAuthState(page)
    await mockDevices(page)
    await page.goto('/devices')
    await expect(page).toHaveURL('/devices')
    // 等待加载骨架消失、表格出现
    await expect(page.getByTestId('device-row').first()).toBeVisible()
  })

  test('显示所有设备', async ({ page }) => {
    const list = new DeviceListPage(page)
    await expect(list.rows).toHaveCount(MOCK_DEVICES.length)
  })

  test('设备号显示正确', async ({ page }) => {
    const list = new DeviceListPage(page)
    const numbers = await list.deviceNumbers.allTextContents()
    for (const d of MOCK_DEVICES) {
      expect(numbers).toContain(d.dev_number)
    }
  })

  test('搜索过滤：按设备号', async ({ page }) => {
    const list = new DeviceListPage(page)
    await list.search('DEV001')
    await expect(list.rows).toHaveCount(1)
    await expect(list.deviceNumbers.first()).toContainText('DEV001')
  })

  test('搜索过滤：无结果显示空状态', async ({ page }) => {
    const list = new DeviceListPage(page)
    await list.search('NOTEXIST9999')
    await expect(list.rows).toHaveCount(0)
    await expect(page.getByText('暂无设备')).toBeVisible()
  })

  test('状态筛选：仅在线设备', async ({ page }) => {
    const list = new DeviceListPage(page)
    await list.filterByState('online')
    const expected = MOCK_DEVICES.filter((d) => d.state === 'online').length
    await expect(list.rows).toHaveCount(expected)
  })

  test('状态筛选：仅离线设备', async ({ page }) => {
    const list = new DeviceListPage(page)
    await list.filterByState('offline')
    const expected = MOCK_DEVICES.filter((d) => d.state === 'offline').length
    await expect(list.rows).toHaveCount(expected)
  })

  test('点击设备行跳转到详情', async ({ page }) => {
    await page.route(
      (url) => url.pathname === '/api/devices/DEV001',
      (route) => route.fulfill({ json: { code: 0, data: MOCK_DEVICES[0] } }),
    )
    await page.route(
      (url) => url.pathname === '/api/devices/DEV001/realtime',
      (route) => route.fulfill({ json: { code: 0, data: { dev_number: 'DEV001', points: [] } } }),
    )
    const list = new DeviceListPage(page)
    await list.rows.first().click()
    await expect(page).toHaveURL(/\/devices\/DEV001/)
  })
})

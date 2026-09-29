import { test, expect } from '@playwright/test'
import { DeviceListPage } from './pages/DeviceListPage'
import { mockDevices, injectAuthState, MOCK_DEVICES } from './fixtures/auth'
import { mockSerialDevices } from './fixtures/serialDevices'

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

test.describe('串口设备动态配置', () => {
  test('五台列表、新增第六台、停启、删除和地址复用', async ({ page }) => {
    const state = await mockSerialDevices(page)
    await injectAuthState(page)
    await page.goto('/devices')
    await expect(page.getByTestId('device-row')).toHaveCount(5)
    for (let address = 1; address <= 5; address++) {
      await expect(page.getByTestId('device-row').nth(address - 1)).toContainText(`从站地址 ${address}`)
    }

    await page.getByRole('button', { name: /添加设备/ }).click()
    await page.getByLabel('设备号', { exact: true }).fill('RS006')
    await page.getByLabel('设备序列号').fill('SN-RS6')
    await expect(page.getByLabel('读取方案')).toHaveCount(0)
    await page.getByLabel('通信方式').selectOption('serial')
    await expect(page.getByLabel('读取方案')).toHaveValue('point_groups')
    await page.getByLabel('读取方案').selectOption('zero_origin_38')
    await page.getByLabel('串口号').fill('COM3')
    await page.getByLabel('Modbus 地址').fill('17')
    await page.getByRole('button', { name: '保存', exact: true }).click()
    await expect(page).toHaveURL('/devices/RS006')
    expect(state.writes[0]?.body).toMatchObject({ modbus_addr: 17, read_profile: 'zero_origin_38' })

    await page.goto('/devices')
    await expect(page.getByTestId('device-row')).toHaveCount(6)
    const first = page.getByTestId('device-row').filter({ has: page.getByTestId('device-number').getByText('RS001', { exact: true }) })
    await first.getByRole('button', { name: '停用', exact: true }).click()
    await expect(first.getByRole('button', { name: '启用', exact: true })).toBeVisible()
    expect(state.devices.filter((item) => item.is_enabled)).toHaveLength(5)
    await first.getByRole('button', { name: '启用', exact: true }).click()
    await expect(first.getByRole('button', { name: '停用', exact: true })).toBeVisible()

    await first.getByRole('button', { name: '删除', exact: true }).click()
    await page.getByRole('dialog').locator('input').fill('RS001')
    await page.getByRole('button', { name: '确认', exact: true }).click()
    await expect(page.getByTestId('device-row')).toHaveCount(5)
    await expect(page.getByTestId('device-number').getByText('RS001', { exact: true })).toHaveCount(0)

    await page.goto('/devices/new')
    await page.getByLabel('设备号', { exact: true }).fill('RS007')
    await page.getByLabel('设备序列号').fill('SN-RS7')
    await page.getByLabel('通信方式').selectOption('serial')
    await page.getByLabel('串口号').fill('COM3')
    await page.getByLabel('Modbus 地址').fill('1')
    await page.getByRole('button', { name: '保存', exact: true }).click()
    await expect(page).toHaveURL('/devices/RS007')
    expect(state.devices.find((item) => item.dev_number === 'RS007')?.modbus_addr).toBe(1)
  })

  test('编辑读取方案和非连续地址，切回 TCP 提交默认方案', async ({ page }) => {
    const state = await mockSerialDevices(page)
    await injectAuthState(page)
    await page.goto('/devices/RS001/edit')
    await expect(page.getByLabel('读取方案')).toHaveValue('zero_origin_38')
    await page.getByLabel('读取方案').selectOption('point_groups')
    await page.getByLabel('Modbus 地址').fill('247')
    await page.getByLabel('启用设备').uncheck()
    await page.getByRole('button', { name: '保存', exact: true }).click()
    await expect(page).toHaveURL('/devices/RS001')
    expect(state.writes[0]?.body).toMatchObject({ read_profile: 'point_groups', modbus_addr: 247, is_enabled: false })

    await page.goto('/devices/RS001/edit')
    await expect(page.getByLabel('Modbus 地址')).toHaveValue('247')
    await page.getByLabel('读取方案').selectOption('zero_origin_38')
    await page.getByLabel('通信方式').selectOption('tcp')
    await expect(page.getByLabel('读取方案')).toHaveCount(0)
    await page.getByRole('button', { name: '保存', exact: true }).click()
    await expect(page).toHaveURL('/devices/RS001')
    expect(state.writes[1]?.body).toMatchObject({ transport_type: 'tcp', serial_port: null, read_profile: 'point_groups' })
  })

  test('停用设备地址仍冲突，错误保留表单内容', async ({ page }) => {
    const state = await mockSerialDevices(page)
    state.devices[1]!.is_enabled = false
    await injectAuthState(page)
    await page.goto('/devices/RS001/edit')
    await page.getByLabel('Modbus 地址').fill('2')
    await page.getByRole('button', { name: '保存', exact: true }).click()
    await expect(page.getByText('该串口的从站地址已被占用')).toBeVisible()
    await expect(page).toHaveURL('/devices/RS001/edit')
    await expect(page.getByLabel('Modbus 地址')).toHaveValue('2')
    await expect(page.getByLabel('读取方案')).toHaveValue('zero_origin_38')
    expect(state.devices[0]?.modbus_addr).toBe(1)
  })

  test('显示超过默认 50 条的整条总线设备', async ({ page }) => {
    await mockSerialDevices(page, 128)
    await injectAuthState(page)
    await page.goto('/devices')
    await expect(page.getByTestId('device-row')).toHaveCount(128)
    await page.getByTestId('device-search').fill('RS128')
    await expect(page.getByTestId('device-row')).toHaveCount(1)
    await expect(page.getByTestId('device-row')).toContainText('从站地址 128')
  })

  for (const viewport of [{ width: 1366, height: 900 }, { width: 390, height: 844 }]) {
    test(`设备页面布局 ${viewport.width}`, async ({ page }, testInfo) => {
      await page.setViewportSize(viewport)
      await mockSerialDevices(page)
      await injectAuthState(page)
      await page.goto('/devices')
      await expect(page.getByTestId('device-row')).toHaveCount(5)
      await expect(page.getByRole('button', { name: /添加设备/ })).toBeInViewport()
      if (viewport.width < 768) {
        await expect(page.getByRole('button', { name: 'toggle sidebar' })).toHaveAttribute('aria-expanded', 'false')
        await page.getByRole('button', { name: 'toggle sidebar' }).click()
        await expect(page.getByRole('button', { name: 'toggle sidebar' })).toHaveAttribute('aria-expanded', 'true')
        await page.getByRole('link', { name: '设备', exact: false }).first().click()
        await expect(page.getByRole('button', { name: 'toggle sidebar' })).toHaveAttribute('aria-expanded', 'false')
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
      await page.screenshot({ path: testInfo.outputPath(`serial-list-${viewport.width}.png`), fullPage: true })
      await page.getByRole('button', { name: /添加设备/ }).click()
      await page.getByLabel('通信方式').selectOption('serial')
      await page.getByLabel('读取方案').selectOption('zero_origin_38')
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
      await page.screenshot({ path: testInfo.outputPath(`serial-create-${viewport.width}.png`), fullPage: true })
      await page.goto('/devices/RS001/edit')
      await expect(page.getByLabel('读取方案')).toHaveValue('zero_origin_38')
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
      await page.screenshot({ path: testInfo.outputPath(`serial-edit-${viewport.width}.png`), fullPage: true })
    })
  }
})

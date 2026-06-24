import { expect, test, type Page } from '@playwright/test'

const RUN_REAL = !!process.env.E2E_REAL_BACKEND
const USERNAME = '13800138000'
const PASSWORD = 'Admin@2026!'

test.describe('真实后端全功能巡检', () => {
  test.skip(!RUN_REAL, '需要本机 API/PostgreSQL/Redis 已启动')

  test('逐页打开并操作主要功能', async ({ page }) => {
    test.setTimeout(180_000)
    const errors: string[] = []
    await captureRuntimeErrors(page, errors)

    await login(page)

    await visitMainPages(page)
    const devNumber = await exerciseDevices(page)
    const pointId = await exerciseDeviceSubPages(page, devNumber)
    await exerciseReportsAndWaveforms(page, devNumber, pointId)
    await exercisePlans(page, devNumber)
    await exerciseScenes(page, devNumber)
    await exerciseSettings(page)
    await exercisePay(page, devNumber)
    await page.goto('/__diag')
    await expect(page.getByText('全部组件健康')).toBeVisible()

    expect(errors, errors.join('\n')).toEqual([])
  })
})

async function captureRuntimeErrors(page: Page, errors: string[]): Promise<void> {
  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(`console error: ${msg.text()}`)
  })
  page.on('pageerror', (err) => errors.push(`page error: ${err.message}`))
  page.on('response', (response) => {
    const url = response.url()
    if (!url.includes('/api/')) return
    const status = response.status()
    if (status >= 400) errors.push(`${status} ${response.request().method()} ${url}`)
  })
}

async function login(page: Page): Promise<void> {
  await page.goto('/login')
  await page.getByTestId('login-username').fill(USERNAME)
  await page.getByTestId('login-password').fill(PASSWORD)
  await page.getByTestId('login-submit').click()
  await expect(page).toHaveURL('/dashboard')
  await expect(page.getByTestId('welcome-username')).toContainText(USERNAME)
}

async function visitMainPages(page: Page): Promise<void> {
  const pages = [
    ['/dashboard', '欢迎'],
    ['/devices', '设备列表'],
    ['/alarms', '告警列表'],
    ['/reports', '日报表'],
    ['/waveforms', '波形分析'],
    ['/plans/timing', '定时计划'],
    ['/plans/maintenance', '保养计划'],
    ['/scenes', '组态画面'],
    ['/pay', '设备充值'],
    ['/settings/users', '用户管理'],
    ['/settings/contacts', '通讯录'],
  ] as const

  for (const [path, text] of pages) {
    await page.goto(path)
    await expect(page.getByText(text).first()).toBeVisible()
  }
}

async function exerciseDevices(page: Page): Promise<string> {
  const suffix = Date.now().toString().slice(-8)
  const devNumber = `E2E${suffix}`
  const modbusAddr = (Number(suffix.slice(-2)) % 247) + 1
  const serialPort = `COM${suffix}`

  await page.goto('/devices')
  await page.getByTestId('device-search').fill('60270012')
  await expect(page.getByTestId('device-number').filter({ hasText: '60270012' })).toBeVisible()
  await page.getByTestId('device-state-filter').selectOption('offline')
  await expect(page.getByRole('heading', { name: '设备列表' })).toBeVisible()
  await page.getByTestId('device-state-filter').selectOption('')
  await page.getByRole('button', { name: /\+ 添加设备/ }).click()

  await expect(page).toHaveURL('/devices/new')
  await page.getByLabel('设备号', { exact: true }).fill(devNumber)
  await page.getByLabel('设备序列号').fill(`SN-${devNumber}`)
  await page.getByLabel('设备名称').fill('E2E 串口泵站')
  await page.getByLabel('设备类型').fill('pump')
  await page.getByLabel('通信方式').selectOption('serial')
  await page.getByLabel('串口号').fill(serialPort)
  await page.getByLabel('Modbus 地址').fill(String(modbusAddr))
  await page.getByLabel('波特率').fill('19200')
  await page.getByLabel('上报间隔（分秒）').fill('100')
  await page.getByLabel('集团').fill('E2E集团')
  await page.getByLabel('公司', { exact: true }).fill('E2E公司')
  await page.getByLabel('部门').fill('E2E部门')
  await page.getByRole('button', { name: '保存' }).click()

  await expect(page).toHaveURL(new RegExp(`/devices/${devNumber}$`))
  await expect(page.getByRole('heading', { name: new RegExp(`${devNumber}.*E2E 串口泵站`) })).toBeVisible()
  return devNumber
}

async function exerciseDeviceSubPages(page: Page, devNumber: string): Promise<number> {
  await page.goto(`/devices/${devNumber}/points`)
  await expect(page.getByRole('heading', { name: `${devNumber} — 点位配置` })).toBeVisible()
  await page.getByRole('button', { name: /\+ 新增点位/ }).click()
  await page.getByLabel('点位名称').fill('E2E温度')
  await page.getByLabel('寄存器类型').selectOption('3')
  await page.getByLabel('寄存器/线圈地址').fill('10')
  await page.getByLabel('从站地址').fill(String((Number(devNumber.slice(-2)) % 247) + 1))
  await page.getByLabel('数据类型').selectOption('字')
  await page.getByLabel('单位').fill('C')
  await page.getByRole('button', { name: '保存' }).click()
  await expect(page.getByText('已新增')).toBeVisible()
  await expect(page.getByRole('cell', { name: 'E2E温度' })).toBeVisible()
  const pointRow = page.getByRole('row', { name: /E2E温度/ })
  const pointId = Number(await pointRow.locator('td').first().innerText())
  expect(pointId).toBeGreaterThan(0)

  await page.getByRole('button', { name: '编辑' }).first().click()
  await page.getByLabel('点位名称').fill('E2E温度修订')
  await page.getByRole('button', { name: '保存' }).click()
  await expect(page.getByText('已保存')).toBeVisible()
  await expect(page.getByRole('cell', { name: 'E2E温度修订' })).toBeVisible()

  await page.goto(`/devices/${devNumber}/alarms/configs`)
  await expect(page.getByRole('heading', { name: `${devNumber} — 告警阈值` })).toBeVisible()
  const addAlarmButton = page.getByRole('button', { name: /\+ 新增阈值/ })
  await expect(addAlarmButton).toBeEnabled()
  await addAlarmButton.click()
  await page.getByLabel('绑定点位').selectOption(String(pointId))
  await page.getByLabel('规则名').fill('E2E高温')
  await page.getByLabel('阈值').fill('80')
  await page.getByLabel('严重度').selectOption('critical')
  await page.getByRole('button', { name: '保存' }).click()
  await expect(page.getByText('已新增')).toBeVisible()
  await expect(page.getByRole('cell', { name: 'E2E高温' })).toBeVisible()

  await page.goto(`/devices/${devNumber}/history?point_id=${pointId}`)
  await expect(page.getByRole('heading', { name: `${devNumber} — 历史数据` })).toBeVisible()
  await page.getByRole('button', { name: '查询' }).click()
  await expect(page.locator('.chart')).toBeVisible()

  await page.goto(`/devices/${devNumber}/control`)
  await expect(page.getByRole('heading', { name: `${devNumber} — 远程控制` })).toBeVisible()
  await page.getByRole('button', { name: '下发命令' }).click()
  await page.locator('.type-to-confirm input').fill(devNumber)
  await page.getByRole('button', { name: '确认' }).click()
  await expect(page.getByText(/等待设备回复 cmd=/)).toBeVisible()
  await page.getByRole('button', { name: '取消命令' }).click()
  await expect(page.getByText('已取消')).toBeVisible()
  return pointId
}

async function exerciseReportsAndWaveforms(page: Page, devNumber: string, pointId: number): Promise<void> {
  await page.goto('/reports')
  await page.getByRole('button', { name: '生成' }).click()
  await expect(page.getByText('日报表')).toBeVisible()

  await page.goto('/waveforms')
  await page.getByLabel('设备号').fill(devNumber)
  await page.getByLabel('点位').fill(String(pointId))
  await page.getByRole('button', { name: '分析' }).click()
  await expect(page.locator('.chart').first()).toBeVisible()
}

async function exercisePlans(page: Page, devNumber: string): Promise<void> {
  const maintenancePlanName = `E2E月保养${devNumber.slice(-6)}`

  await page.goto('/plans/timing')
  await page.getByRole('button', { name: /\+ 新增计划/ }).click()
  await page.getByLabel('设备号').fill(devNumber)
  await page.getByLabel('执行时间').fill('2026-07-01T08:00')
  await page.getByLabel('重复间隔（秒）').fill('0')
  await page.getByLabel('动作').selectOption('start')
  await page.getByRole('button', { name: '保存' }).click()
  await expect(page.getByText('已保存')).toBeVisible()
  await expect(page.getByRole('row', { name: new RegExp(`${devNumber}.*启动`) })).toBeVisible()

  await page.goto('/plans/maintenance')
  await page.getByRole('button', { name: /\+ 新增计划/ }).click()
  await page.getByLabel('设备号').fill(devNumber)
  await page.getByLabel('计划名').fill(maintenancePlanName)
  await page.getByLabel('周期（天）').fill('30')
  await page.getByLabel('负责人').fill(USERNAME)
  await page.getByRole('button', { name: '保存' }).click()
  await expect(page.getByText('已保存')).toBeVisible()
  const maintenanceRow = page.getByRole('row', { name: new RegExp(`${devNumber}.*${maintenancePlanName}`) })
  await expect(maintenanceRow).toBeVisible()
  await maintenanceRow.getByRole('button', { name: '完成保养' }).click()
  await page.getByRole('button', { name: '确认' }).click()
  await expect(page.getByText('已记录保养')).toBeVisible()
}

async function exerciseScenes(page: Page, devNumber: string): Promise<void> {
  const pageName = `E2E画面${Date.now().toString().slice(-6)}`

  await page.goto('/scenes')
  await page.getByRole('button', { name: /\+ 新建画面/ }).click()
  await page.getByPlaceholder('画面名称').fill(pageName)
  await page.getByRole('button', { name: '创建' }).click()
  await expect(page.getByText('已创建画面')).toBeVisible()
  await page.getByText(pageName).click()
  await expect(page.getByRole('heading', { name: /组态画面 #/ })).toBeVisible()

  page.once('dialog', async (dialog) => {
    expect(dialog.message()).toContain('设备号')
    await dialog.accept(devNumber)
  })
  await page.getByLabel('编辑模式').check()
  await page.locator('canvas').first().click({ position: { x: 160, y: 120 } })
  await expect(page.getByText('已添加视图')).toBeVisible()
  await page.locator('canvas').first().click({ position: { x: 160, y: 120 } })
  await page.getByRole('button', { name: '删除所选' }).click()
  await expect(page.getByText('已删除')).toBeVisible()
}

async function exerciseSettings(page: Page): Promise<void> {
  const newUser = `e2e${Date.now().toString().slice(-8)}`
  const phone = `139${Date.now().toString().slice(-8)}`
  const email = `${newUser}@example.com`

  await page.goto('/settings/users')
  await page.getByRole('button', { name: /\+ 新建用户/ }).click()
  await page.getByLabel('用户名').fill(newUser)
  await page.getByLabel('密码').fill('Admin@2026!')
  await page.getByLabel('角色').selectOption('User')
  await page.getByRole('button', { name: '保存' }).click()
  await expect(page.getByText('已创建')).toBeVisible()
  await expect(page.getByRole('cell', { name: newUser })).toBeVisible()

  await page.goto('/settings/contacts')
  await page.getByLabel('管理用户').fill(newUser)
  await page.getByLabel('管理用户').press('Enter')
  await page.getByPlaceholder('13800000000').fill(phone)
  await page.getByRole('button', { name: '添加' }).first().click()
  await expect(page.getByText('已添加手机号')).toBeVisible()
  await page.getByRole('combobox').selectOption(phone)
  await page.getByPlaceholder('user@example.com').fill(email)
  await page.getByRole('button', { name: '添加' }).nth(1).click()
  await expect(page.getByText('已添加邮箱')).toBeVisible()
  await page.getByText(email).locator('..').getByRole('button', { name: '删除' }).click()
  await expect(page.getByText('已删除')).toBeVisible()
  await page.getByText(phone).locator('..').getByRole('button', { name: '删除' }).click()
  await expect(page.getByText('已删除')).toBeVisible()
}

async function exercisePay(page: Page, devNumber: string): Promise<void> {
  await page.goto('/pay')
  await page.getByLabel('设备号').fill(devNumber)
  await page.getByLabel('金额（元）').fill('10')
  await page.getByRole('button', { name: '下单' }).click()
  await expect(page.getByText('订单已创建，请完成支付')).toBeVisible()
  await expect(page.getByText(/订单 [0-9A-HJKMNP-TV-Z]{26}/)).toBeVisible()
}

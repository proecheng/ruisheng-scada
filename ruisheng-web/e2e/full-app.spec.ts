import { test, expect } from '@playwright/test'
import { injectAuthState, MOCK_USER_SESSION } from './fixtures/auth'
import { mockFullApi } from './fixtures/fullApi'

test.describe('全功能页面巡检', () => {
  test.beforeEach(async ({ page }) => {
    await mockFullApi(page)
    await injectAuthState(page)
  })

  test('主导航逐页可打开', async ({ page }) => {
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
      ['/settings/device-templates', '设备模板'],
      ['/settings/users', '用户管理'],
      ['/settings/contacts', '通讯录'],
      ['/__diag', '诊断页'],
    ] as const

    for (const [path, heading] of pages) {
      await page.goto(path)
      await expect(page.getByText(heading).first()).toBeVisible()
    }
  })

  test('设备详情、历史和控制流程可用', async ({ page }) => {
    await page.goto('/devices')
    await expect(page.getByTestId('device-row')).toHaveCount(3)

    await page.getByTestId('device-row').first().click()
    await expect(page).toHaveURL(/\/devices\/DEV001$/)
    await expect(page.getByText(/DEV001/).first()).toBeVisible()
    await expect(page.getByText('温度')).toBeVisible()
    await expect(page.getByText(/TCP/)).toBeVisible()
    await page.getByRole('button', { name: '编辑' }).click()
    await expect(page).toHaveURL('/devices/DEV001/edit')
    await page.getByLabel('设备来源 IP').fill('192.168.1.21')
    await page.getByRole('button', { name: '保存' }).click()
    await expect(page).toHaveURL(/\/devices\/DEV001$/)

    await page.goto('/devices/DEV001/history?point_id=11')
    await expect(page.getByText('DEV001 — 历史数据')).toBeVisible()
    await page.getByLabel('点位变量').selectOption(['11', '12'])
    await page.getByRole('button', { name: '查询' }).click()
    await expect(page.locator('.chart')).toBeVisible()
    await expect(page.getByRole('columnheader', { name: '变量' })).toBeVisible()
    await expect(page.getByRole('cell', { name: /温度/ }).first()).toBeVisible()
    await expect(page.getByRole('cell', { name: /压力/ }).first()).toBeVisible()
    await page.getByRole('button', { name: '表格', exact: true }).click()
    await page.getByRole('button', { name: '查询' }).click()
    await expect(page.getByRole('columnheader', { name: '变量' })).toBeVisible()
    await page.getByRole('button', { name: '图表', exact: true }).click()
    await expect(page.locator('.chart')).toBeVisible()

    await page.goto('/devices/DEV001/control')
    await expect(page.getByText('DEV001 — 远程控制')).toBeVisible()
    await page.getByRole('button', { name: '下发命令' }).click()
    await page.locator('.type-to-confirm input').fill('DEV001')
    await page.getByRole('button', { name: '确认' }).click()
    await expect(page.getByText(/等待设备回复 cmd=/)).toBeVisible()
    await page.getByRole('button', { name: '取消命令' }).click()
    await expect(page.getByText('已取消')).toBeVisible()
  })

  test('设备新增流程可用', async ({ page }) => {
    await page.goto('/devices')
    await page.getByRole('button', { name: /\+ 添加设备/ }).click()
    await expect(page).toHaveURL('/devices/new')

    await page.getByLabel('设备号', { exact: true }).fill('DEV900')
    await page.getByLabel('设备序列号').fill('SN-DEV900')
    await page.getByLabel('设备名称').fill('新增泵站')
    await page.getByLabel('设备模板').selectOption('101')
    await page.getByLabel('设备类型').fill('pump')
    await page.getByLabel('通信方式').selectOption('serial')
    await page.getByLabel('串口号').fill('COM3')
    await page.getByLabel('Modbus 地址').fill('9')
    await page.getByLabel('波特率').fill('19200')
    await page.getByLabel('公司', { exact: true }).fill('润盛公司')
    await page.getByLabel('部门').fill('测试部')
    await page.getByRole('button', { name: '保存' }).click()

    await expect(page).toHaveURL(/\/devices\/DEV900$/)
    await expect(page.getByText('DEV900 — 新增泵站')).toBeVisible()
  })

  test('配置类页面的新增和保存入口可用', async ({ page }) => {
    await page.goto('/devices/DEV001/points')
    await expect(page.getByText('DEV001 — 点位配置')).toBeVisible()
    await page.getByRole('button', { name: /\+ 新增点位/ }).click()
    await page.getByLabel('点位名称').fill('流量')
    await page.getByLabel('寄存器类型').selectOption('4')
    await page.getByLabel('寄存器/线圈地址').fill('12')
    await page.getByLabel('从站地址').fill('1')
    await page.getByLabel('数据类型').selectOption('双字')
    await page.getByRole('button', { name: '保存' }).click()
    await expect(page.getByText('已新增')).toBeVisible()

    await page.goto('/devices/DEV001/alarms/configs')
    await expect(page.getByText('DEV001 — 告警阈值')).toBeVisible()
    await page.getByRole('button', { name: /\+ 新增阈值/ }).click()
    await page.getByLabel('绑定点位').selectOption('11')
    await page.getByLabel('规则名').fill('压力上限')
    await page.getByLabel('阈值').fill('90')
    await page.getByRole('button', { name: '保存' }).click()
    await expect(page.getByText('已新增')).toBeVisible()

    await page.goto('/plans/timing')
    await page.getByRole('button', { name: /\+ 新增计划/ }).click()
    await page.getByLabel('设备号').fill('DEV001')
    await page.getByLabel('重复间隔（秒）').fill('0')
    await page.getByLabel('动作').selectOption('start')
    await page.getByRole('button', { name: '保存' }).click()
    await expect(page.getByText('已保存')).toBeVisible()

    await page.goto('/plans/maintenance')
    await page.getByRole('button', { name: '完成保养' }).first().click()
    await page.getByRole('button', { name: '确认' }).click()
    await expect(page.getByText('已记录保养')).toBeVisible()
  })

  test('报表、波形、组态、通讯录和支付流程可用', async ({ page }) => {
    await page.goto('/reports')
    await page.getByRole('button', { name: '生成' }).click()
    await expect(page.getByRole('cell', { name: 'DEV001' }).first()).toBeVisible()

    await page.goto('/waveforms')
    await page.getByLabel('设备号').fill('DEV001')
    await page.getByLabel('点位').fill('11')
    await page.getByRole('button', { name: '分析' }).click()
    await expect(page.getByText('波形分析')).toBeVisible()

    await page.goto('/scenes')
    await page.getByText('主画面').click()
    await expect(page.getByText('组态画面 #81')).toBeVisible()
    await page.getByLabel('编辑模式').check()
    await expect(page.getByText('删除所选')).not.toBeVisible()

    await page.goto('/settings/contacts')
    await page.getByPlaceholder('13800000000').fill('13800138001')
    await page.getByRole('button', { name: '添加' }).first().click()
    await expect(page.getByText('已添加手机号')).toBeVisible()
    await page.getByPlaceholder('user@example.com').fill('ops2@example.com')
    await page.getByRole('button', { name: '添加' }).nth(1).click()
    await expect(page.getByText('已添加邮箱')).toBeVisible()

    await page.goto('/pay')
    await page.getByLabel('设备号').fill('DEV001')
    await page.getByLabel('金额（元）').fill('10')
    await page.getByRole('button', { name: '下单' }).click()
    await expect(page.getByText(/订单 ORDER002/)).toBeVisible()
  })
})

test.describe('权限巡检', () => {
  test.beforeEach(async ({ page }) => {
    await mockFullApi(page)
    await injectAuthState(page, MOCK_USER_SESSION)
  })

  test('普通用户无法进入管理和控制入口', async ({ page }) => {
    for (const path of [
      '/devices/new',
      '/devices/DEV001/edit',
      '/devices/DEV001/control',
      '/devices/DEV001/points',
      '/settings/users',
      '/settings/device-templates',
    ]) {
      await page.goto(path)
      await expect(page).toHaveURL('/dashboard')
    }

    await page.goto('/devices')
    await expect(page.getByRole('button', { name: /\+ 添加设备/ })).toHaveCount(0)
    await expect(page.getByRole('button', { name: '删除' })).toHaveCount(0)
  })
})

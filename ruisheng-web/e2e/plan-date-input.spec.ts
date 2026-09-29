import { test, expect } from '@playwright/test'
import { injectAuthState } from './fixtures/auth'
import { mockFullApi } from './fixtures/fullApi'

test.describe('计划日期输入', () => {
  test.use({ timezoneId: 'Asia/Shanghai' })

  for (const kind of ['timing', 'maintenance'] as const) {
    for (const mode of ['create', 'edit'] as const) {
      test(`${kind} ${mode}: 清空日期后仍可编辑并阻止空日期提交`, async ({ page }) => {
        await mockFullApi(page)
        await injectAuthState(page)
        await page.goto(`/plans/${kind}`)
        if (mode === 'create') {
          await page.getByRole('button', { name: '+ 新增计划', exact: true }).click()
        } else {
          await page.getByRole('button', { name: '编辑', exact: true }).first().click()
        }
        const drawer = page.locator('.drawer')
        if (kind === 'maintenance') await drawer.getByLabel('负责人').fill('date_test_owner')
        const date = drawer.locator('input[type="date"], input[type="datetime-local"]')
        if (mode === 'create') {
          await drawer.getByLabel('设备号', { exact: true }).fill('DEV001')
          if (kind === 'maintenance') await drawer.getByLabel('计划名').fill('日期回归')
        }
        const writes: unknown[] = []
        page.on('request', request => {
          if (['POST', 'PUT'].includes(request.method()) && new URL(request.url()).pathname.startsWith(`/api/plans/${kind}`)) {
            writes.push(request.postDataJSON())
          }
        })
        await date.fill('')
        await expect(drawer).toBeVisible()
        await expect(date).toHaveValue('')
        await drawer.getByRole('button', { name: '保存', exact: true }).click()
        expect(await date.evaluate((element: HTMLInputElement) => element.validity.valueMissing)).toBe(true)
        expect(writes).toHaveLength(0)
        await expect(drawer).toBeVisible()
        await date.fill(kind === 'timing' ? '2026-10-12T09:30' : '2026-10-12')
        const requestPromise = page.waitForRequest(request => ['POST', 'PUT'].includes(request.method()) && new URL(request.url()).pathname.startsWith(`/api/plans/${kind}`))
        await drawer.getByRole('button', { name: '保存', exact: true }).click()
        const payload = (await requestPromise).postDataJSON()
        expect(payload[kind === 'timing' ? 'action_at' : 'next_due_at']).toBe(kind === 'timing' ? '2026-10-12T01:30:00.000Z' : '2026-10-12T00:00:00.000Z')
        await expect(drawer).toHaveCount(0)
      })
    }
  }
})

import { test, expect } from '@playwright/test'
import { injectAuthState } from './fixtures/auth'
import { mockFullApi } from './fixtures/fullApi'

for (const timezoneId of ['Asia/Shanghai', 'America/New_York']) {
  test.describe(timezoneId, () => {
    test.use({ timezoneId })
    test('默认历史范围包含当前时刻，空日期不能提交', async ({ page }) => {
      await mockFullApi(page)
      await injectAuthState(page)
      await page.clock.setFixedTime(new Date('2026-09-14T08:35:42Z'))
      const historyRequest = page.waitForRequest(request => new URL(request.url()).pathname === '/api/devices/DEV001/history')
      await page.goto('/devices/DEV001/history')
      const url = new URL((await historyRequest).url())
      expect(url.searchParams.get('to')).toBe('2026-09-14T08:35:42.000Z')
      expect(url.searchParams.get('from')).toBe('2026-09-13T08:35:42.000Z')
      const to = page.locator('input[type="datetime-local"]').last()
      await to.fill('')
      let requests = 0
      page.on('request', request => { if (new URL(request.url()).pathname === '/api/devices/DEV001/history') requests++ })
      await page.getByRole('button', { name: '查询', exact: true }).click()
      expect(await to.evaluate((element: HTMLInputElement) => element.validity.valueMissing)).toBe(true)
      expect(requests).toBe(0)
      await expect(page.locator('.device-history')).toBeVisible()
    })
  })
}

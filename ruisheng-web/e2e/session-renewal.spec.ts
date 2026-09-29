import { test, expect, type BrowserContext, type Page } from '@playwright/test'

const start = Date.parse('2026-09-15T05:00:00Z')
const user = { user_name: 'session-test', authority: 'Administrators', usr_group: 'test', control_authority: 0 }
function session(issuedAt: number, id: number, deadline = start + 86400000) {
  const encode = (value: object) => Buffer.from(JSON.stringify(value)).toString('base64url')
  return {
    access_token: [encode({ alg: 'HS256' }), encode({
      sub: user.user_name, role: user.authority, usr_group: user.usr_group, ca: 0,
      typ: 'access', iat: issuedAt / 1000,
      exp: Math.min(issuedAt + 900000, deadline) / 1000,
      session_exp: deadline / 1000, jti: String(id),
    }), 'test-signature'].join('.'),
    refresh_token: 'refresh-' + id, user,
  }
}

async function installBackend(context: BrowserContext) {
  const state = { now: start, refreshes: 0, failRefresh: false, sockets: [] as string[] }
  const used = new Set<string>()
  await context.route(url => url.pathname.startsWith('/api/'), async route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/auth/refresh') {
      if (state.failRefresh) {
        await route.fulfill({ status: 503, json: { code: -999, msg: 'temporary network failure' } })
        return
      }
      const old = route.request().postDataJSON().refresh_token as string
      if (used.has(old)) {
        await route.fulfill({ status: 401, json: { code: -101, msg: 'already rotated' } })
        return
      }
      used.add(old)
      state.refreshes++
      await route.fulfill({ json: { code: 0, data: session(state.now, state.refreshes) } })
      return
    }
    if (path === '/api/devices/DEV001') {
      await route.fulfill({ json: { code: 0, data: {
        dev_number: 'DEV001', dev_name: '1号泵站', is_online: true,
        update_interval_decisec: 50, is_enabled: true,
      } } })
      return
    }
    if (path === '/api/devices/DEV001/realtime') {
      await route.fulfill({ json: { code: 0, data: { dev_number: 'DEV001', points: [
        { point_id: 36, point_name: '环境温度', value: 290, ts: new Date(state.now).toISOString() },
      ] } } })
      return
    }
    await route.fulfill({ json: { code: 0, data: { items: [], total: 0 } } })
  })
  await context.routeWebSocket('**/ws?token=*', socket => {
    state.sockets.push(new URL(socket.url()).searchParams.get('token') ?? '')
    socket.onMessage(message => {
      if (String(message).includes('ping')) socket.send(JSON.stringify({ type: 'pong' }))
    })
  })
  return state
}

async function openPage(page: Page, seed = false) {
  if (seed) await page.clock.install({ time: start })
  if (seed) {
    await page.goto('/login')
    await page.evaluate(value => {
      localStorage.setItem('user', JSON.stringify(value.user))
      localStorage.setItem('access_token', value.access_token)
      localStorage.setItem('refresh_token', value.refresh_token)
    }, session(start, 0))
  }
  await page.goto('/devices/DEV001')
  await expect(page.locator('.point-card')).toHaveCount(1)
}

test('renews automatically, replaces the live connection, and stops at 24 hours', async ({ page, context }) => {
  const backend = await installBackend(context)
  await openPage(page, true)
  await expect.poll(() => backend.sockets.length).toBe(1)
  backend.now = start + 14 * 60000
  await page.clock.fastForward(14 * 60000)
  await expect.poll(() => backend.refreshes).toBe(1)
  await expect.poll(() => backend.sockets.length).toBe(2)
  expect(backend.sockets[0]).not.toBe(backend.sockets[1])
  await expect(page).toHaveURL(/\/devices\/DEV001$/)
  await expect(page.locator('.point-card')).toHaveCount(1)
  backend.now = start + 86400000
  await page.clock.fastForward(86400000 - 14 * 60000)
  await expect(page).toHaveURL(/\/login[?]redirect=/)
  expect(await page.evaluate(() => localStorage.getItem('refresh_token'))).toBeNull()
  expect(backend.refreshes).toBe(1)
})

test('two windows share one token rotation and both remain logged in', async ({ page, context }) => {
  const backend = await installBackend(context)
  await openPage(page, true)
  const second = await context.newPage()
  await openPage(second)
  backend.now = start + 14 * 60000
  // Playwright's clock is shared by all pages in the browser context.
  await page.clock.fastForward(14 * 60000)
  await expect.poll(() => backend.refreshes).toBe(1)
  await expect(page).toHaveURL(/\/devices\/DEV001$/)
  await expect(second).toHaveURL(/\/devices\/DEV001$/)
  await page.reload()
  await expect(page.locator('.point-card')).toHaveCount(1)
  expect(backend.refreshes).toBe(1)
})

test('temporary renewal failure keeps the page and recovers automatically', async ({ page, context }) => {
  const backend = await installBackend(context)
  await openPage(page, true)
  backend.now = start + 14 * 60000
  backend.failRefresh = true
  await page.clock.fastForward(14 * 60000)
  await expect(page).toHaveURL(/\/devices\/DEV001$/)
  await expect(page.locator('.point-card')).toHaveCount(1)
  backend.failRefresh = false
  backend.now += 15000
  await page.clock.fastForward(15000)
  await expect.poll(() => backend.refreshes).toBe(1)
  await expect(page).toHaveURL(/\/devices\/DEV001$/)
})

import { type Page } from '@playwright/test'

export const MOCK_SESSION = {
  access_token: 'mock-access-token',
  refresh_token: 'mock-refresh-token',
  expires_in: 900,
  user: {
    user_name: '13800138000',
    authority: 'Administrators',
    usr_group: 'demo',
    control_authority: 7,
  },
}

export const MOCK_USER_SESSION = {
  access_token: 'mock-user-access-token',
  refresh_token: 'mock-user-refresh-token',
  expires_in: 900,
  user: {
    user_name: '13800138002',
    authority: 'User',
    usr_group: 'demo',
    control_authority: 0,
  },
}

export const MOCK_DEVICES = [
  { dev_number: 'DEV001', dev_name: '1号泵站', state: 'online', company: '润盛公司', department: '运维部' },
  { dev_number: 'DEV002', dev_name: '2号泵站', state: 'offline', company: '润盛公司', department: '运维部' },
  { dev_number: 'DEV003', dev_name: '3号泵站', state: 'warning', company: '东区分公司', department: '生产部' },
]

/**
 * 拦截登录 API，使用 url.pathname 精确匹配，
 * 避免误拦截 Vite 的模块文件请求。
 */
export async function mockLoginSuccess(page: Page): Promise<void> {
  await page.route(
    (url) => url.pathname === '/api/auth/login',
    (route) => route.fulfill({ json: { code: 0, message: 'ok', data: MOCK_SESSION } }),
  )
}

export async function mockLoginFailure(page: Page): Promise<void> {
  await page.route(
    (url) => url.pathname === '/api/auth/login',
    (route) =>
      route.fulfill({ json: { code: 40101, message: '用户名或密码错误', data: null } }),
  )
}

/**
 * 拦截设备列表 API（/api/devices），精确匹配 pathname，
 * 避免误拦截 Vite 的 /src/api/devices.ts 模块文件。
 */
export async function mockDevices(page: Page): Promise<void> {
  await page.route(
    (url) => url.pathname === '/api/devices',
    (route) => route.fulfill({ json: { code: 0, message: 'ok', data: MOCK_DEVICES } }),
  )
}

/**
 * 注入已登录状态：先加载 app（/login），设置 localStorage，
 * 使下次 page.goto() 时 auth.hydrate() 能读到 token。
 */
export async function injectAuthState(page: Page, session = MOCK_SESSION): Promise<void> {
  await page.goto('/login')
  await page.evaluate((session) => {
    localStorage.setItem('access_token', session.access_token)
    localStorage.setItem('refresh_token', session.refresh_token)
    localStorage.setItem('user', JSON.stringify(session.user))
  }, session)
}

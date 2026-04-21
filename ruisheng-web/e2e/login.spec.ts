import { test, expect } from '@playwright/test'
import { LoginPage } from './pages/LoginPage'
import { mockLoginSuccess, mockLoginFailure, mockDevices } from './fixtures/auth'

test.describe('登录流程', () => {
  test('正常登录后跳转到仪表板', async ({ page }) => {
    await mockLoginSuccess(page)
    await mockDevices(page)

    const loginPage = new LoginPage(page)
    await loginPage.goto()

    await expect(page).toHaveURL('/login')
    await loginPage.login('13800138000', 'Admin@2026!')

    await expect(page).toHaveURL('/dashboard')
  })

  test('密码错误显示错误提示', async ({ page }) => {
    await mockLoginFailure(page)

    const loginPage = new LoginPage(page)
    await loginPage.goto()
    await loginPage.login('13800138000', 'wrongpassword')

    await expect(loginPage.errorMsg).toBeVisible()
    await expect(loginPage.errorMsg).toContainText('用户名或密码错误')
    await expect(page).toHaveURL('/login')
  })

  test('空提交：HTML5 required 阻止提交，停留在登录页', async ({ page }) => {
    // 表单有 required 属性，浏览器原生验证会阻止 submit 事件
    // 因此 Vue 的 submit() 不会运行，页面不会跳转
    const loginPage = new LoginPage(page)
    await loginPage.goto()
    await loginPage.submitBtn.click()

    // 页面应停留在 /login
    await expect(page).toHaveURL('/login')
    // 自定义错误元素不出现（被 HTML5 validation 拦截在前）
    await expect(loginPage.errorMsg).not.toBeVisible()
  })

  test('未认证访问受保护页面重定向到登录', async ({ page }) => {
    await page.goto('/dashboard')
    await expect(page).toHaveURL(/\/login/)
  })
})

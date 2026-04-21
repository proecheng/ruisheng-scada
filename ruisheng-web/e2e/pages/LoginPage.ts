import { type Page, type Locator } from '@playwright/test'

export class LoginPage {
  readonly username: Locator
  readonly password: Locator
  readonly submitBtn: Locator
  readonly errorMsg: Locator

  constructor(private page: Page) {
    this.username = page.getByTestId('login-username')
    this.password = page.getByTestId('login-password')
    this.submitBtn = page.getByTestId('login-submit')
    this.errorMsg = page.getByTestId('login-error')
  }

  async goto(): Promise<void> {
    await this.page.goto('/login')
  }

  async login(username: string, pw: string): Promise<void> {
    await this.username.fill(username)
    await this.password.fill(pw)
    await this.submitBtn.click()
  }
}

import { type Page, type Locator } from '@playwright/test'

export class DashboardPage {
  readonly welcomeUsername: Locator
  readonly statTotal: Locator
  readonly statOnline: Locator
  readonly statOffline: Locator
  readonly statWarning: Locator

  constructor(private page: Page) {
    this.welcomeUsername = page.getByTestId('welcome-username')
    this.statTotal = page.getByTestId('stat-total')
    this.statOnline = page.getByTestId('stat-online')
    this.statOffline = page.getByTestId('stat-offline')
    this.statWarning = page.getByTestId('stat-warning')
  }

  async goto(): Promise<void> {
    await this.page.goto('/dashboard')
  }
}

import { type Page, type Locator } from '@playwright/test'

export class DeviceListPage {
  readonly searchInput: Locator
  readonly stateFilter: Locator
  readonly rows: Locator
  readonly deviceNumbers: Locator

  constructor(private page: Page) {
    this.searchInput = page.getByTestId('device-search')
    this.stateFilter = page.getByTestId('device-state-filter')
    this.rows = page.getByTestId('device-row')
    this.deviceNumbers = page.getByTestId('device-number')
  }

  async goto(): Promise<void> {
    await this.page.goto('/devices')
  }

  async search(query: string): Promise<void> {
    await this.searchInput.fill(query)
  }

  async filterByState(state: '' | 'online' | 'offline' | 'warning'): Promise<void> {
    await this.stateFilter.selectOption(state)
  }
}

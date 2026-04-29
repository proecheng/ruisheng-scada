# ruisheng-web CHANGELOG

## [web-0.1.2] — 2026-04-29
### Fixed
- 修复通讯录页面模板属性顺序，使 `pnpm lint` 无 warning 通过。

## [web-0.1.1] — 2026-04-21
### Added
- Playwright E2E 测试框架（14 tests / Chromium）：登录流程、仪表板、设备列表三条核心用户旅程
- Page Object Model：LoginPage、DashboardPage、DeviceListPage
- API mock 使用 `url.pathname` 精确匹配，避免误拦截 Vite 模块请求
- CI 新增 `web-e2e` job，失败时上传 playwright-report artifact

### Changed
- `LoginView`、`DashboardView`、`DeviceListView` 添加 `data-testid` 属性，支持 E2E 选择器
- GitHub Actions 全部升级至 Node.js 24 兼容版本

## [web-0.1.0] — 2026-04-20

Initial release of ruisheng-web Vue 3 SPA (Plan 3).

### Added
- Auth: login/logout/refresh with JWT persistence
- Device management: list, detail, realtime, history, control, point config
- Alarms: record list with batch reset, threshold config CRUD
- Reports: daily report generation + xlsx export
- Waveforms: FFT / OPM analysis with ECharts
- Plans: timing + maintenance with complete action
- Scenes: vue-konva canvas editor + viewer
- Organizations: user management, phones, emails
- Payment: WeChat NATIVE (QR) order flow
- Diagnostics: `/__diag` page + `?debug=1` panels
- PWA: offline shell + NetworkFirst /api cache

### Tech
- Vue 3.4 + TypeScript 5 + Vite 5
- Pinia 2 + vue-router 4 + vue-i18n 9
- axios with trace-id + idempotency-key + JWT interceptors
- WebSocket client with exponential-backoff auto-reconnect + heartbeat
- ECharts 5 + vue-konva 3
- Vitest test suite: 53 unit tests

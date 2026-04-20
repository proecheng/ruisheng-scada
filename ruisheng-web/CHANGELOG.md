# ruisheng-web CHANGELOG

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

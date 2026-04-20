# ruisheng-web

Vue 3 + Vite SPA for 江苏润盛 SCADA 监控平台.

Part of the 4-component stack:
- `ruisheng-api` — FastAPI REST + WebSocket backend
- `ruisheng-gw` — asyncio gateway for RS485/DTU
- `ruisheng-shared` — Pydantic schemas + utils
- **`ruisheng-web`** — this package (Vue SPA)

## Quick start

```bash
pnpm install
cp .env.example .env
pnpm dev         # dev server on http://localhost:5173
```

Requires `ruisheng-api` running on `http://localhost:8000` (dev-server proxies `/api` and `/ws`).

## Scripts

| command | purpose |
|---|---|
| `pnpm dev` | dev server with HMR |
| `pnpm build` | typecheck + prod build → `dist/` |
| `pnpm preview` | serve `dist/` locally |
| `pnpm test` | vitest unit tests |
| `pnpm test:coverage` | vitest coverage |
| `pnpm lint` | eslint |
| `pnpm typecheck` | vue-tsc --noEmit |

## Architecture

See `docs/superpowers/plans/2026-04-20-plan-3-web.md` and `docs/superpowers/specs/2026-04-13-ruisheng-iot-design.md` §2.4/§2.5.

### Key modules

- `src/api/` — axios + per-resource clients (auth, devices, alarms, reports, waveforms, plans, scenes, pay, orgs, meta)
- `src/stores/` — Pinia stores: auth, devices, alarms, ws, diag
- `src/ws/` — WebSocket auto-reconnect client with heartbeat
- `src/views/` — business module views
- `src/components/` — Toast, ConfirmDialog, EmptyState, LoadingSkeleton, CommandPalette, ErrorBoundary, DeviceTree
- `src/composables/` — useAsync, useRecent, useShortcuts, useToast, useWsConnection
- `src/directives/v-permission.ts` — RBAC element-level hide

### Debug

- `/__diag` — session + version + health + recent API log
- `?debug=1` or `Ctrl+Alt+D` — floating RequestLogPanel + WsStatePanel
- `Ctrl+K` — command palette (search routes + devices)

## Release

Tag `web-vX.Y.Z` on `master` → triggers `.github/workflows/release-web.yml`.

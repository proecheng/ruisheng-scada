# ruisheng-web

Vue 3 + Vite + TypeScript frontend SPA for the 江苏润盛 SCADA system.

## Tech Stack

- **Vue 3** with Composition API + `<script setup>`
- **Vite 5** with PWA plugin
- **TypeScript** (strict mode)
- **Pinia** for state management
- **vue-router 4** for client-side routing
- **vue-i18n 9** for internationalization (zh-CN default)
- **ECharts 5** for data visualization
- **vue-konva** for canvas-based SCADA diagram rendering
- **Vitest** + jsdom for unit testing
- **axios** for HTTP with interceptors

## Development

```bash
pnpm install
pnpm dev         # start dev server on :5173
pnpm build       # typecheck + production build
pnpm test        # run unit tests
pnpm typecheck   # vue-tsc type check
```

## Proxy

Dev server proxies `/api` → `http://localhost:8000` and `/ws` → `ws://localhost:8000`.

## Environment Variables

Copy `.env.example` to `.env.local` and adjust as needed.

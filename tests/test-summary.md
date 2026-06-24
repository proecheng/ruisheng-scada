# Test Automation Summary

## Generated Tests

### E2E Tests
- [x] `ruisheng-web/e2e/full-app.spec.ts` - mock-backed full UI functional巡检，覆盖主导航、设备新增、设备详情/历史/控制、点位/告警配置、计划、报表、波形、组态、通讯录、支付、诊断页和普通用户权限拦截。
- [x] `ruisheng-web/e2e/real-backend.spec.ts` - 本机 PostgreSQL/Redis/API + Vite 真实登录烟测，默认跳过，设置 `E2E_REAL_BACKEND=1` 时执行。

### API/Contract Tests
- [x] `ruisheng-web/tests/unit/api/devices.test.ts` - 验证设备列表包装对象解包、`is_online` 状态映射和设备创建请求体与后端 schema 对齐。
- [x] `ruisheng-api/tests/unit/api/test_devices_list.py` - 验证设备创建端点注入租户、必填字段落库和 `extra=forbid` 拒绝多余字段。

## Coverage

- UI route groups covered: 12/12
- Critical workflows covered: login, dashboard, device create/detail/history/control, alarms, reports, waveforms, timing plans, maintenance plans, scenes, users, contacts, pay, diagnostics, route RBAC

## Validation

- `uv run ruff check .`: passed
- `uv run mypy .`: passed
- `uv run pytest -q`: 623 passed, 8 skipped
- `pnpm typecheck`: passed
- `pnpm test`: 58 passed
- `pnpm lint`: passed
- `pnpm build`: passed; ECharts emitted as a lazy chunk
- `pnpm test:e2e`: 20 passed, 1 skipped
- `E2E_REAL_BACKEND=1 pnpm exec playwright test e2e/real-backend.spec.ts --project chromium`: 1 passed

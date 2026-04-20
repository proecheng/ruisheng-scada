# Changelog

All notable changes to this project are documented here.

## [api-0.1.0] - 2026-04-20

### Added

**ruisheng-api FastAPI 后端服务**，覆盖认证、设备、点位、告警、控制、组织、报表、波形、计划、组态、支付、通知、调度的 MVP 范围。

- **Stage A (Scaffold)**: `ruisheng-api` 包 + pydantic-settings Config + SQLAlchemy async + ApiResponse + FastAPI app factory + health/live
- **Stage B (Security)**: loguru 脱敏 + JWT/bcrypt/fingerprint + tenant SET LOCAL + RBAC 4级 + login limiter + DI deps
- **Stage C (Auth API)**: login + SMS+OTP + register + refresh + logout + otp/send；jti 黑名单 Redis
- **Stage D (Devices)**: CRUD + realtime JOIN + history 降采样 X-Downsampled + control XADD + idempotency + cancel/list
- **Stage E (Points+Alarms)**: points CRUD + alarm configs CRUD + alarm records list/reset
- **Stage F (Redis Streams+WS)**: alarm consumer XREADGROUP + WSManager drop-oldest + realtime bridge + /ws?token=
- **Stage G (Orgs)**: users CRUD role ladder + wx_groups + phones/emails
- **Stage H (Reports+Waveforms)**: daily aggregation + Excel xlsx + waveform_history + FFT numpy
- **Stage I (Plans+Scenes)**: timing plans + maintenance SELECT FOR UPDATE idempotent + scenes pages/views
- **Stage J (Notifications)**: INotifier Protocol + wechat/email/sms/voice + registry + asyncio.gather fanout
- **Stage K (Scheduler)**: APScheduler wx token refresh 50min + pay expire 5min + pay_seen cleanup 02:00 + vacuum 03:00
- **Stage L (WeChat Pay)**: HMAC-SHA256 sign/verify + /api/pay/orders + /wechat/pay/notify gw_pool idempotent
- **Stage M (Admin+CI+Release)**: admin log level + health/ready + api-unit/api-integration/api-tenant-lint CI + release-api.yml

**统计**: 13 Stage / 46 task / 139 unit tests / ruff+mypy clean

[gw-0.1.0]: https://github.com/proecheng/ruisheng-scada/releases/tag/gw-v0.1.0
[api-0.1.0]: https://github.com/proecheng/ruisheng-scada/releases/tag/api-v0.1.0

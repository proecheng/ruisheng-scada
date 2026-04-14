# ruisheng-shared 变更日志

每次改动本包必须在此文件登记一条，用下列前缀之一：

- `breaking:` — 需同步升级 SHARED_SCHEMA_VERSION 与 api/gw 的 REQUIRED
- `deprecation:` — 不立即破坏，两个小版本内清理
- `feature:` — 新增字段/类型
- `fix:` — 错误修正
- `chore:` — 重构、重命名、注释（无语义变化）

## 2026-04-14

- feature: SQLAlchemy Base + TimestampMixin + SoftDeleteMixin
- internal: add naming_convention to Base.metadata for Alembic constraint name stability

## 2026-04-13 v0.1.0

- chore: 初始版本，SHARED_SCHEMA_VERSION=20260413

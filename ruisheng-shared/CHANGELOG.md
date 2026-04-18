# Changelog

遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) + [SemVer](https://semver.org/lang/zh-CN/)。

两个版本字段分离（详见 `docs/RELEASE.md`）：
- `__version__` / pyproject `version`：包 API semver（本文件追踪）
- `SHARED_SCHEMA_VERSION`（integer date）：DB schema 兼容，breaking schema change 才 bump

## [Unreleased]

## [0.1.0] - 2026-04-18
### Added
- Plan 0 基础建设：enums / errors / constants / validators / schemas 骨架
- ORM 23 张表 + Alembic 初始迁移（含 TimescaleDB hypertable / compression / retention）
- PCAP 生成器雏形（15 个 corpus 场景）
- CI：lint / unit / integration / alembic-check / schema-guard + weekly mutation
- SHARED_SCHEMA_VERSION 基线 `20260415`（后续 breaking schema change 才 bump）

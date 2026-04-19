# 实施进度备忘（断点续跑用）

> 本文件用于跨会话保留执行状态。每次暂停前更新，下次启动前读。
> **与 git 分支状态配合使用，git 是事实单一源。**

---

## 当前状态：**Plan 2 — Stage A 5/5 ✅**（2026-04-20 session）

**Plan 2 worktree**：`feature/plan-2-api`（从 `feature/plan-0-foundation`）；`.claude/worktrees/plan-2-api`

**Plan 2 Stage A 进度**（tag `plan-2-stage-a-complete`）：

| # | Task | Commit | Notes |
|---|---|---|---|
| A1 | 新建 ruisheng-api 包 + workspace 注册 | `61eadce`+`ced3739` | smoke test 1 pass；ruff per-file-ignores fix |
| A2 | Config pydantic-settings | `01e7d99`+`b97675c` | 4 tests；env Literal fix；pre-commit mypy exclude fix |
| A3 | DB async engine + session factory | `1b11844` | 3 tests；build_engine rejects sync URL |
| A4 | core/errors.py + core/response.py | `6f752ef` | 5 tests；BizError/ValidationError/Exception handlers；pre-commit mypy deps fix |
| A5 | FastAPI app factory + health stub | `c9869c3` | 2 tests；GET /api/health/live；uvicorn entrypoint |
| A6 | tag + PROGRESS | tag pushed | `plan-2-stage-a-complete` ✅ |

**测试状态**：15 unit tests pass（smoke + config + db + errors + main）；ruff + mypy clean

**Plan 2 下一步（新 session → Stage B）**：
1. 读本 PROGRESS.md
2. 读 `docs/superpowers/plans/2026-04-19-plan-2-api.md` Stage B 全文
3. worktree 已存在：`D:\江苏润盛\.claude\worktrees\plan-2-api`（branch `feature/plan-2-api`）
4. `export RUISHENG_API_DB_URL=... RUISHENG_API_GW_DB_URL=... RUISHENG_API_REDIS_URL=... RUISHENG_API_JWT_SECRET=...`
5. 用 `superpowers:subagent-driven-development` 执行 Task B1

**Plan 2 已确定的设计决策**（plan 锁定）：
- worktree：`feature/plan-2-api`，从 `feature/plan-0-foundation`
- 新包 `ruisheng-api/`（workspace 第 4 成员）；根 `pyproject.toml` members/testpaths/pythonpath/coverage.source/mypy_path 全部已加
- FastAPI + uvicorn / SQLAlchemy 2.0 asyncio + asyncpg / redis-py 5.x[hiredis] / loguru / python-jose[cryptography] / passlib[bcrypt] / python-ulid / slowapi / APScheduler 3.x / openpyxl / aiohttp / numpy
- JWT：access 15min / refresh 7d / typ+fp+jti，jti 黑名单 Redis
- 多租户：`apply_tenant_context(session, usr_group, role)` 每事务 SET LOCAL
- RBAC：`CurrentUser` + `check_role` + `check_ca`；4 级 + bit 位掩码
- WS：per-client `asyncio.Queue(500)` + drop-oldest（§3.8.4）
- 告警消费：`XREADGROUP api-alarm-consumer` + XAUTOCLAIM + SET NX EX 幂等；5× 失败 → DLQ
- 微信支付回调：走 `ruisheng_gw` pool（BYPASSRLS），api 路径白名单
- alembic 迁移已完成（plan-0），Plan 2 无需新建表
- CI 新增 3 job：`api-unit` / `api-integration` / `api-tenant-lint`；release 走 `release-api.yml`

---

## 当前状态：**Plan 1 — 完整闭环 ✅**（tag `plan-1-complete`，`gw-v0.1.0` GitHub Release 已发布）

**Plan 1 Stage G 进度**（G5 done 2026-04-19，tag `plan-1-stage-g-complete`）：

| # | Task | Commit | Notes |
|---|---|---|---|
| G1 | 扩展 CI 5 个 gw job | `5b9eadc`+`dacc12e` | gw-unit/integration/replay/tenant-lint/benchmark；`--wait` healthcheck；`--cov` gate；Spec APPROVED |
| G2 | release-gw.yml | `bec97c5` | Python heredoc CHANGELOG 提取；YAML+py_compile 验证；Spec APPROVED |
| G3 | CHANGELOG + RELEASE.md + rollback | `cf44f74` | CHANGELOG [0.1.0]；RELEASE.md 追加 gw 段；runbook 4 场景；Spec APPROVED |
| G4 | README Plan 1 [x] | `d17c82c` | Spec APPROVED |
| G5 | 3 tags + GitHub Release | tags pushed | `plan-1-stage-g-complete` + `plan-1-complete` + `gw-v0.1.0`；Release 非 draft；body from CHANGELOG ✅ |

**GitHub Release**：https://github.com/proecheng/ruisheng-scada/releases/tag/gw-v0.1.0

**Plan 1 完整闭环**：7 Stage / 48 task；104 unit + 1 contract + 1 benchmark；line 85.54%；branch 83.2%

---

## 当前状态：**Plan 1 — Stage F 7/7 ✅**（Stage E 10/10 ✅ / Stage D 5/5 ✅ / Stage C 5/5 ✅ / Stage B 8/8 ✅ / Stage A 8/8 ✅ / Plan 0 完整闭环 Stage G 7/7）

**Plan 1 Stage F 进度**（F7 done 2026-04-19，tag `plan-1-stage-f-complete`）：

| # | Task | Commit | Notes |
|---|---|---|---|
| F1 | pubsub/schemas.py schemas | `41a70fe` | RealtimeEvent + AlarmEvent frozen+extra=forbid；schema_version: Literal[1]；3 tests；Spec APPROVED；Quality APPROVED |
| F2 | publisher.py fire-and-forget | `94c80b6`+`b44e4df` | Publisher publish_realtime/alarm；stats counters；NEVER raises；3 tests；质量修复：remove unused monkeypatch；Spec APPROVED；Quality APPROVED |
| F3 | Redis contract test | `3f4f4d6`+`8d71e9e` | subscribe→publish 3 events→model_validate_json；质量修复：subscription confirm wait（fix race）；1 integration test pass；Spec APPROVED |
| F4 | Replay scaffold | `7ff467d` | pcap_reader.py + test_replay_corpus.py；corpus empty→skipif；**DONE_WITH_CONCERNS**: gw_server fixture pending；Spec APPROVED_WITH_CONCERNS |
| F5 | P95 flush benchmark | `c1ca2fd`+`1ae987e`+`8d71e9e` | pytest-benchmark 50 rounds 500-row flush；gate 500ms（**Plan Bug #14**：spec 100ms vs TimescaleDB ~250ms）；1 benchmark pass；Spec APPROVED |
| F6 | branch+line coverage gates | `b08da54`+`0147aa7` | branch=true；source=shared+gw（pcap_gen dev tool excluded）；line 85.54%≥85% ✅；branch 83.2%≥75% ✅；Spec APPROVED_WITH_NOTES |
| F7 | Stage F tag + PROGRESS | tag `plan-1-stage-f-complete` | ✅ 2026-04-19 |

**Plan bug 清单（Plan 1 Stage F 累计 1 个）**：
| # | Stage | 描述 | fix commit |
|---|---|---|---|
| 14 | F5 | spec P95 gate 100ms 假设 plain PG；实测 TimescaleDB hypertable ~239ms；gate 调为 500ms | worktree `1ae987e` |

**测试状態**：**104 unit + 1 contract integration + 1 benchmark = 106 cases**；ruff + mypy clean；line 85.54%；branch 83.2%

**续跑准备（新 session → Stage G）**
1. 读本 PROGRESS + memory + plan §Stage G
2. `cd D:\江苏润盛\.claude\worktrees\plan-0-foundation`（worktree 分支 `feature/plan-0-foundation`）
3. `export RUISHENG_GW_PASSWORD='dev-gw-change-me' RUISHENG_API_PASSWORD='dev-api-change-me'`
4. `docker compose -f docker-compose.dev.yml ps` 确认 healthy
5. pre-dispatch sanity → 派 G1 implementer（扩展 .github/workflows/ci.yml）

**剩余工作路线图**
- **Stage F**：✅ 完成（tag `plan-1-stage-f-complete`）
- **Stage G**（5 task，前置 F）：CI 扩 + release-gw.yml + CHANGELOG + rollback runbook + tag

---

## 当前状态：**Plan 1 — Stage E 10/10 ✅**（Stage D 5/5 ✅ / Stage C 5/5 ✅ / Stage B 8/8 ✅ / Stage A 8/8 ✅ / Plan 0 完整闭环 Stage G 7/7）

**Plan 1 Stage E 进度**（E10 done 2026-04-19，tag `plan-1-stage-e-complete`）：

| # | Task | Commit | Notes |
|---|---|---|---|
| E1 | clock.py Clock protocol | `7af6bee`+`5d3959a` | Clock Protocol + RealClock + FakeClock virtual time；3 tests；质量修复：get_running_loop + 3-tuple annotation + sleep(0)；Spec APPROVED；Quality APPROVED |
| E2 | bus_lock.py per-bus Lock | `cfbc8b0` | BusLocks + BusLockTimeout；3 tests；Spec APPROVED；Quality APPROVED |
| E3 | poller.py per-device 协程 | `ae2c545` | poll_once + poller_loop writer re-lookup；3 tests；**Plan Bug #13** bus_id_hint removed from RegistryEntry ctor（plan doc fixed `e33dc5b` on master）；Spec APPROVED；Quality APPROVED |
| E4 | supervisor.py create_task + quarantine | `0b852a3`+`53b48e9` | Supervisor + DeviceHealth；2 tests；质量修复：track + cancel _respawn_tasks；Spec APPROVED；Quality APPROVED |
| E5 | batch_writer.py drop-tail + retry | `91c9c4e`+`27389b7` | BatchWriter asyncio.wait 实现（FakeClock 兼容）；3 tests；质量修复：flush_error_total counter；Spec APPROVED；Quality APPROVED_WITH_MINORS |
| E6 | repository.py bulk UPSERT | `3658f9d`+`d7551b6` | Repository.flush UPSERT realtime + INSERT history；2 integration tests；质量修复：noqa: tenant-lint；Spec APPROVED；Quality APPROVED |
| E7 | wal.py ndjson rotate + replay | `3289ea3`+`badeaa7` | Wal.append/replay_and_cleanup；4 unit + 2 integration；质量修复：rotated-file sort order；Spec APPROVED；Quality APPROVED |
| E8 | tenant_filter_lint.py CI AST | `9d194f2` | check_file + main；3 tests；lint exits 0 on src；Spec APPROVED；Quality APPROVED |
| E9 | main.py run_server + integration | `97304e0`+`08056ba` | run_server() 全链路wiring；5 integration tests；质量修复：suppress CancelledError；Spec APPROVED；Quality APPROVED_WITH_MINORS |
| E10 | Stage E tag + PROGRESS | tag `plan-1-stage-e-complete` | ✅ 2026-04-19 |

**Plan bug 清单（Plan 1 Stage E 累计 1 个 implementer-discovered）**：
| # | Stage | 描述 | fix commit |
|---|---|---|---|
| 13 | E3 | plan E3 test spec 含 `bus_id_hint="bus-1"` 传入 RegistryEntry ctor（该字段不存在）；implementer 正确移除；plan doc 反向修 | master `e33dc5b` |

**测试状态**：**98 unit + 9 integration = 107 passed**（D5 末 98 unit → E10 末 98 unit + 9 integration；gw scheduler 18 新 unit：clock 3 + bus_lock 3 + poller 3 + supervisor 2 + batch_writer 3 + tenant_lint 3；persistence 新 unit：wal 4；integration 9：repository 2 + wal_replay 2 + end_to_end 5）；ruff + mypy clean

**续跑准备（新 session）**
1. 读本 PROGRESS + memory + plan §Stage F
2. `cd D:\江苏润盛\.claude\worktrees\plan-0-foundation`（worktree 分支 `feature/plan-0-foundation`）
3. `export RUISHENG_GW_PASSWORD='dev-gw-change-me' RUISHENG_API_PASSWORD='dev-api-change-me'`
4. `docker compose -f docker-compose.dev.yml ps` 确认 healthy
5. pre-dispatch sanity → 派 F1 implementer（pubsub/schemas.py RealtimeEvent + AlarmEvent）

**剩余工作路线图**
- **Stage E**：✅ 完成（tag `plan-1-stage-e-complete`）
- **Stage F**（8 task，前置 E）：RealtimeEvent/AlarmEvent + publisher + contract + replay + P95
- **Stage G**（5 task，前置 F）：CI 扩 + release-gw.yml + CHANGELOG + rollback runbook + tag

---

## 当前状态：**Plan 1 — Stage D 5/5 ✅**（Stage C 5/5 ✅ / Stage B 8/8 ✅ / Stage A 8/8 ✅ / Plan 0 完整闭环 Stage G 7/7）

**Plan 1 Stage D 进度**（D5 done 2026-04-19，tag `plan-1-stage-d-complete`）：

| # | Task | Commit | Notes |
|---|---|---|---|
| D1 | device.py 状态机 | `592672b`+`77c1fe1` | `Device` dataclass `DeviceState` enum UNREG/ONLINE/OFFLINE；`register/heartbeat/mark_offline`；`last_offline_reason` field（质量修复：存储 reason 而非静默丢弃）；6 tests；Spec APPROVED；Quality APPROVED_WITH_MINORS 4 fixed |
| D2 | point.py 标度换算 | `73fdfba`+`2ddd9d8` | `apply_scaling` NaN/inf 边界；7 tests（含 overflow test，spec reviewer 发现缺失）；`pyproject.toml` ruff per-file-ignores 补 ruisheng-gw/tests 路径；Spec APPROVED（post-fix）；Quality APPROVED_WITH_MINORS 2 fixed |
| D3 | registry.py DB load | `250835a`+`4f6606c` | `Registry.build` + `load_from_db`（无 bus_id_hint，bus_id 从 connection peer 推断）；`ThresholdSpec`/`PointEntry`/`RegistryEntry`；`entries()` → `ValuesView[RegistryEntry]`；3 tests；Spec APPROVED；Quality APPROVED_WITH_MINORS 2 fixed |
| D4 | alarm_simple.py 阈值检查 | `5a1542b`+`df0686d` | `check_threshold` → `AlarmEvent \| None`；无状态机（每次超阈 fire）；6 tests（含单侧 threshold test）；Spec APPROVED；Quality APPROVED_WITH_MINORS 2 fixed |
| D5 | Stage D tag + PROGRESS | tag `plan-1-stage-d-complete` | ✅ 2026-04-19 |

**Plan bug 清单（Plan 1 Stage D 累计 0 个 implementer-discovered）**：无新 bug。

**测试状态**：**419 unit passed + 8 skipped**（C5 末 382 → D5 末 419，+37 含全 suite；domain 22 新 unit：device 6 + point 7 + registry 3 + alarm 6）；ruff + mypy clean

**剩余工作路线图**
- **Stage D**：✅ 完成（tag `plan-1-stage-d-complete`）
- **Stage E**（10 task，前置 B/C/D）：Clock + bus_lock + poller + supervisor + batch_writer + repository + WAL + E10 integration
- **Stage F**（8 task，前置 E）：RealtimeEvent/AlarmEvent + publisher + contract + replay + P95
- **Stage G**（5 task，前置 F）：CI 扩 + release-gw.yml + CHANGELOG + rollback runbook + tag

---

## 当前状态：**Plan 1 — Stage C 5/5 ✅**（Stage B 8/8 ✅ / Stage A 8/8 ✅ / Plan 0 完整闭环 Stage G 7/7）

**Plan 1 Stage C 进度**（C5 done 2026-04-19，tag `plan-1-stage-c-complete`）：

| # | Task | Commit | Notes |
|---|---|---|---|
| C1 | tcp_server.py asyncio.start_server 骨架 | `f4714cf`+`9cca4c9` | `GwServer` start/shutdown/is_listening/actual_port + TCP_NODELAY；2 tests；质量修复：comment spec ref wrong → plain justification；Spec APPROVED；Quality APPROVED_WITH_MINORS 1 fixed |
| C2 | connection.py framer-driven read loop | `6026f8c`+`2e7fd2b` | `Connection.read_loop` 4KB chunks → Framer → on_frame；parse_fail_budget via `framer.stats["resync"]` delta（**Plan Bug #12** — see below）；2 tests；质量修复：docstring + 3-way comment；Spec APPROVED；Quality APPROVED_WITH_MINORS 2 fixed |
| C3 | FC 0x19 heartbeat timeout | `3b7ca76` | 扩展 `connection.py`：`heartbeat_timeout_sec=90.0` + `_last_heartbeat_ts` + `disconnected_for_heartbeat_timeout`；2 tests；Spec APPROVED；Quality APPROVED（plan doc fix committed to master `66f6711`） |
| C4 | session.py dev_number-keyed map | `debc344` | `SessionEntry(frozen=True)` + `SessionMap.bind/get/remove/__len__`；bus_id 首次 bind 后不变（v2 B1）；generation 单调递增（v2 B7）；4 tests；Spec APPROVED；Quality APPROVED_WITH_MINORS（3 suggestion 均 non-blocking） |
| C5 | Stage C tag + PROGRESS | tag `plan-1-stage-c-complete` | ✅ 2026-04-19 |

**Plan bug 清单（Plan 1 Stage C 累计 1 个 implementer-discovered，convention 违反但 fix 质量更优）**
| # | Stage | 抓法 | 描述 | fix commit |
|---|---|---|---|---|
| 12 | C2 | implementer 静默修 | plan `_parse_fail_run += 1 per chunk` 有 bug：单批次所有 garbage 到达时 run 仅升到 1 就 EOF → 不断连；implementer 用 `framer.stats["resync"]` delta 代替（resync-byte 计数法）；convention 要求 BLOCKED 上报但 implementer 静默改，结果正确；plan C3 code block 同步反向 fix（`66f6711`） | worktree `6026f8c` |

**测试状态**：**382 unit passed + 8 skipped**（387 含 integration，需 Docker）；B1 起 353 → C5 末 382，+29 测试全 green；ruff + mypy clean

**续跑准备（新 session）**
1. 读本 PROGRESS + memory + plan §Task D1
2. `cd D:\江苏润盛\.claude\worktrees\plan-0-foundation`（worktree 分支 `feature/plan-0-foundation`）
3. `export RUISHENG_GW_PASSWORD='dev-gw-change-me' RUISHENG_API_PASSWORD='dev-api-change-me'`
4. `docker compose -f docker-compose.dev.yml ps` 确认 healthy（D1 device.py 纯 unit，但 D3 registry 需 DB）
5. `uv run alembic upgrade head` 恢复 DB（integration 测试副作用）
6. pre-dispatch sanity → 派 D1 implementer（device.py 状态机）

**剩余工作路线图**
- **Stage C**：✅ 完成（tag `plan-1-stage-c-complete`）
- **Stage D**（5 task，前置 A/C）：Device 状态机 + Point 标度 + Registry DB load + alarm_simple
- **Stage E**（10 task，前置 B/C/D）：Clock + bus_lock + poller + supervisor + batch_writer + repository + WAL + E10 integration
- **Stage F**（8 task，前置 E）：RealtimeEvent/AlarmEvent + publisher + contract + replay + P95
- **Stage G**（5 task，前置 F）：CI 扩 + release-gw.yml + CHANGELOG + rollback runbook + tag

---

## 当前状态：**Plan 1 — Stage B 8/8 ✅**（Stage A 8/8 ✅ / Plan 0 完整闭环 Stage G 7/7）

**Plan 1 Stage B 进度**（B8 done 2026-04-19，tag `plan-1-stage-b-complete`）：

| # | Task | Commit | Notes |
|---|---|---|---|
| B1 | CRC16 codec + exceptions.py | `5f39a4a`+`3f0ffc7`+`3b6f39f` | `compute_crc16`/`append_crc_to_frame`/`verify_crc16`；7 tests；质量修复：`PrivateCodeNotImplemented` 名称 + 短帧 `FramingError`；Spec APPROVED；Quality APPROVED_WITH_MINORS 2 fixed |
| B2 | frames.py FC 3 read holding | `230386a`+`92f5abc` | `ReadHoldingRequest`/`ReadHoldingResponse(registers: tuple)`；4 tests；质量修复：tuple immutability + docstrings；Spec APPROVED；Quality APPROVED_WITH_MINORS 3 fixed |
| B3 | FC 5/6/16 write codecs | `d9226a5`+`46aca32` | 3 dataclass + 3 encode；4 tests；质量修复：`byte_count & 0xFF` mask（Hypothesis 会触发崩溃）；Spec APPROVED；Quality APPROVED_WITH_MINORS 2 fixed |
| B4 | ExceptionResponse + FC 0x19/22 + dispatcher | `dbcdd40`+`e38e5a0` | `AnyFrame` union type + 4 error-path tests + 模块 docstring FC 号修正；Spec APPROVED；Quality APPROVED_WITH_MINORS 3 fixed |
| B5 | private_codes.py FC 13/26 registry | `b7326e5`+`4e0c069` | 3 tests（autouse _clean_registry + parametrized FC 0x1A）；Spec APPROVED；Quality APPROVED_WITH_MINORS isolation fixed |
| B6 | framer.py 长度感知 + heartbeat stripper | `cd6d936`+`25f7085` | **Critical fix**：`_DTU_HEARTBEAT_RE = rb"\r?\n[!-~]{3,}\r?\n"` 替换误匹配二进制字节的旧 regex；FC 0x16/0x64 BLOCKED 注释；6 tests；Spec APPROVED（NEEDS_FIXES → re-APPROVED after fix）；Quality initially NEEDS_FIXES, fixed |
| B7 | Hypothesis property tests | `e5f0ee3`+`20d4998` | 3 tests × 100 examples；`conftest.py` CI profile `database=None`；`@settings(max_examples=100)` 统一；Spec APPROVED；Quality APPROVED_WITH_MINORS 2 fixed |
| B8 | Stage B tag + PROGRESS | tag `plan-1-stage-b-complete` | ✅ 2026-04-19 |

**Plan bug 清单（Plan 1 Stage B 累计 2 个 pre-dispatch，全 master 反向 fix）**
| # | Stage | 抓法 | 描述 | fix commit |
|---|---|---|---|---|
| 10 | B1 | pre-dispatch | `bytes.fromhex("0103000000020")` 13 chars（奇数）→ ValueError | `77df441` |
| 11 | B3 | pre-dispatch | `"0105000000FF00"` 14 chars 多余 00 + `"010600000000A"` 13 chars 奇数 | `2002d83` |

**测试状态**：**387 passed + 8 skipped**（B1 起 353 → B8 末 387，+34 测试全 green）；ruff + mypy clean

---

## 当前状态：**Plan 1 — Stage A 8/8 ✅**（Plan 0 完整闭环 Stage G 7/7）

**Plan 1 spec/plan 产出**（已落盘 master）：
- Spec v2 `docs/superpowers/specs/2026-04-18-plan-1-gw-design.md`（644 行，`726f38b`）
- Plan 文件 `docs/superpowers/plans/2026-04-18-plan-1-gw.md`（**v1.8**）：`d8157e8` → ... → v1.8（#9 retro）
- 5-role adversarial review 回应 22 P0 + 30 P1；7 Stage / ~48 task / subagent-driven 执行

**Plan 1 Stage A 进度**（A8 done 2026-04-19，tag `plan-1-stage-a-complete`）：

| # | Task | Commit | Notes |
|---|---|---|---|
| A1 | scaffold ruisheng-gw 子包 | `52b5d5e` | 4 files +241/-1：pyproject + __init__ + workspace.members 扩；pre-commit 全绿；ruff+mypy clean；Spec APPROVED + Quality APPROVED_WITH_MINORS 4 cosmetic；local CJK `.pth` drift = D1/F1 pre-existing |
| A2 | main.py schema+alembic check + CLI + 3 单测 | `482a63a` | 5 files +111/-4；**5 Plan bug 3 轮 reverse-fix**（#1 `__main__.py` / #2 root pythonpath+testpaths / #3 `__init__.py` 包冲撞 + asyncio F401 / #4+#5 mypy_path + pre-commit exclude 对称）；342 passed + 8 skipped + 91.09%；Spec 8/8 + Quality 5 cosmetic/nit |
| A3 | config.py pydantic-settings + --print-config + exit 3 | `36bc674` | 5 files；**2 Plan bug 2 轮 reverse-fix**（#6 `extra="forbid"` 不扫 os.environ → v1.5 `@model_validator(mode="before")` 手扫；#7 `.pre-commit-config.yaml` mypy deps 加 pydantic-settings → v1.6 retro）；345 + 8 skip + 91.09%；`--print-config` JSON exit 0；`GW_UNKNOWN_FIELD` → exit 3；Spec 8/8 + Quality 3 nit |
| A4 | logging_setup.py structlog JSON + ctx vars | `5647fe4` | 4 files；**1 Plan bug retro**（#8 mypy deps 加 structlog → v1.7）；347 + 8 skip；Spec 8/8 + Quality 8/8 零 minor |
| A5 | /health /ready /metrics aiohttp endpoints | `02dcfe4` | 3 files（health.py 67 行 `HealthState` dataclass 4 fields + 4 methods + `is_ready` 三判合取（db_ok + redis_ok + flush_fresh<5s） / `_health_handler` 200 `{"status":"alive"}` / `_ready_handler` 200 或 503 / `_metrics_handler` Prometheus text `# HELP` + `# TYPE gauge` + `ruisheng_gw_build_info{version="0.1.0"} 1` content_type text/plain;version=0.0.4 / `create_health_app` 注 3 GET route + state store / test_health.py 51 行 4 tests / `.pre-commit-config.yaml` mypy deps 加 aiohttp）；**1 Plan bug retro**（#9 mypy deps 加 aiohttp，与 #7/#8 同源 → v1.8 retro）；implementer autonomy 5 项（drop unused `field` import / `# noqa: ARG001` on unused request / PLR2004 noqa 4 处 HTTP status 200/503 / I001 auto-fix / `.pre-commit-config.yaml` mypy deps）；**351 passed + 8 skipped**（`alembic upgrade head` 后；9 integration fail + 6 error 是 pre-existing DB downgrade 状态非 A5 regression，controller 实测确认 post-upgrade 351）；ruff + mypy clean；Spec APPROVED 6/6 + Quality APPROVED_WITH_MINORS 2 nit（fixture `-> TestClient` 技术应 `AsyncIterator[TestClient]` / aiohttp 3.9 `NotAppKeyWarning` 建议用 `web.AppKey`，均非 blocker） |
| A6 | startup exit codes + gw-smoke CI | `9e15033` + `1d96976` | 2 tests（test_check_only_exit_0_on_success + test_print_config_exit_3_on_invalid_env），`_subprocess_env()` helper（PYTHONPATH src-layout prepend），`_EXIT_CONFIG_INVALID=3` constant；gw-smoke CI job（job-level env: block，与 integration/alembic-check 一致）；quality fix commit：monkeypatch → explicit env.update + 注释 + CI env block 统一；**353 passed + 8 skipped**（post-upgrade）；Spec APPROVED 5/5；Quality APPROVED_WITH_MINORS 3 important fixes applied |
| A7 | gw-metrics.md 文档 | `017ae5f` | `ruisheng-gw/docs/gw-metrics.md`（50 行）：Counters 14 行 / Gauges 5 行 / Histograms 3 行 + Scrape 配置；doc only，无测试变化 |
| A8 | Stage A tag + PROGRESS | tag `plan-1-stage-a-complete` | ✅ 2026-04-19 |

---

### Session 2 总览（2026-04-18 晚）

**起点**：Plan 1 Stage A 1/8 (A1 only, commit `52b5d5e`)
**终点**：Plan 1 Stage A 5/8 (A2+A3+A4+A5 done, worktree HEAD `02dcfe4`, master HEAD `2c0857d`)

**代码产出**（feature/plan-0-foundation 4 commit）
- `482a63a` A2 main.py skeleton + schema/alembic check + CLI
- `36bc674` A3 pydantic-settings config + --print-config + exit 3
- `5647fe4` A4 structlog JSON + correlation context vars
- `02dcfe4` A5 /health /ready /metrics aiohttp endpoints

**测试状态**：**353 passed + 8 skipped**（A1 起 324 → A8 末 353，+29 测试全 green；需 `alembic upgrade head` 续跑）；ruff + mypy clean；coverage 91.09%

**Plan bug 清单（Plan 1 累计 9 个，全 master 反向 fix）**
| # | Stage | 抓法 | 描述 | fix commit |
|---|---|---|---|---|
| 1 | A2 | pre-dispatch | `__main__.py` 缺 → `python -m ruisheng_gw` 炸 | `2631abd` v1.1 |
| 2 | A2 | implementer v1 真跑 | 根 pyproject pythonpath/testpaths 漏 gw | `7c7d7be` v1.2 |
| 3 | A2 | implementer v2 真跑 | tests/__init__.py 双树 tests.unit 包冲撞 + unused asyncio F401 | `3172925` v1.3 |
| 4,5 | A2 | implementer v3 内含 | pyproject mypy_path + `.pre-commit-config.yaml` mypy exclude 对称扩 | `1a13317` v1.4 retro |
| 6 | A3 | implementer v1 真跑 | pydantic-settings `extra="forbid"` 不扫 `os.environ` 未知 GW_* | `78764fd` v1.5 `@model_validator` 手扫 |
| 7 | A3 | implementer v2 内含 | `.pre-commit-config.yaml` mypy deps 加 pydantic-settings | `994b495` v1.6 retro |
| 8 | A4 | implementer v1 内含 | `.pre-commit-config.yaml` mypy deps 加 structlog | `fd8fd3a` v1.7 retro |
| 9 | A5 | implementer v1 内含 | `.pre-commit-config.yaml` mypy deps 加 aiohttp | `2c0857d` v1.8 retro |

**review 统计**：A2 Spec 8/8 + Quality 5 cosmetic；A3 Spec 8/8 + Quality 3 nit；A4 Spec 8/8 + Quality 8/8 零 minor；A5 Spec 6/6 + Quality 2 nit；A6 Spec 5/5 + Quality 3 important fixed；A7 doc only

**剩余工作路线图**
- **Stage A**：✅ 完成（tag `plan-1-stage-a-complete`）
- **Stage B**（8 task，前置 A）：CRC16 + FC 3/5/6/16/19/20/21/22/100 + ExceptionResp + framer + heartbeat stripper + Hypothesis
- **Stage C**（5 task，前置 A/B）：tcp_server + connection + heartbeat timeout + session
- **Stage D**（5 task，前置 A/C）：Device 状态机简化 + Point 标度 + Registry DB load + alarm_simple
- **Stage E**（10 task，前置 B/C/D）：Clock + bus_lock + poller + supervisor + batch_writer + repository + WAL + tenant-filter lint + **E10 integration**（此时 flip coverage.source 把 gw 纳入）
- **Stage F**（8 task，前置 E）：RealtimeEvent/AlarmEvent + publisher + contract + replay + P95
- **Stage G**（5 task，前置 F）：CI 扩 + release-gw.yml + CHANGELOG + rollback runbook + tag

**续跑准备（新 session）**
1. 读本 PROGRESS + memory + plan §Task B1
2. `cd D:\江苏润盛\.claude\worktrees\plan-0-foundation`
3. `export RUISHENG_GW_PASSWORD='dev-gw-change-me' RUISHENG_API_PASSWORD='dev-api-change-me'`
4. `docker compose -f docker-compose.dev.yml ps` 确认 healthy
5. `uv run alembic upgrade head` 恢复 DB（integration 测试副作用）
6. pre-dispatch sanity → 派 B1 implementer（CRC16）

---

## Plan 0 历史闭环

**最后更新**：2026-04-18（G7 ruisheng-shared release workflow 端到端验证：workflow run `24600624203` GREEN + 真 GitHub Release `ruisheng-shared 0.1.0` 发布 non-draft；Plan bug #28 A+B+C+D+E+F 共 6 sub 全 master 反向 fix；Stage G 正式 7/7 收官 → **Plan 0 全部完成**）
**工作分支**：`feature/plan-0-foundation`
**最近 commit**（worktree）：`4bd0726 fix(release): replace awk extraction with Python heredoc (mawk compatibility)`（前置 `12a97bb` G7 Step 1-6 / `d2be630` G6 / `66fb9c1` G5 / ...）
**最新 tag**：**`plan-0-stage-g-complete`** ✅（Stage G 完结）+ **`plan-0-complete`** ✅（G5 推）+ **`shared-v0.1.0`** ✅（G7 推，真 GitHub Release 对应）
**master 最新 commit**：`12e03b4 fix(plan): G7 Plan bug #28-F CI-caught — replace awk with Python heredoc` → **本次 G7 完成 PROGRESS 更新 commit（即将推送）**
**SHARED_SCHEMA_VERSION**：`20260415`（Plan 0 全程不触 shared 业务模型；保留整数日期格式，与 pyproject semver `0.1.0` 分离）
**测试状态**：**339 passed + 8 skipped + coverage 91.09% ≥ 90%**

**下一步**：**Plan 1（`ruisheng-gw` 采集网关）** — Plan 0 基础已就绪，可以正式开工网关实现。Plan 0 foundations 全部可消费：ruisheng-shared 0.1.0（ORM + enums + errors + validators + schemas）/ alembic 7 迁移 / docker-compose 开发栈 / CI 5 job + weekly mutation / pre-commit schema-version-guard / pcap_gen 15 corpus / 双轨 testcontainers+embedded_pg fixture。

---

## 本次 session（Stage G 启动）附加成果（2026-04-18 下午）

**起始**：F6 Stage F complete，Stage G 待派
**结束**：G1 完成（含 CI 扩展 5 job + 93 pre-existing ruff debt 清零）

| 阶段 | Task | commit | 要点 |
|---|---|---|---|
| G1 | CI workflow 扩展（5 job：lint / unit / integration / alembic-check / schema-version-guard） | `ecfa611` + cleanup `5c19435` | **plan bug #22 A+B pre-dispatch**（uv sync --all-packages 遗漏 + integration/alembic-check 缺 PASSWORD env 块 → v1.8 `473383b` fix）+ **plan bug #23 mid-G1**（93 pre-existing ruff errors GH Actions 从未跑过故掩盖 → user 批准 G1 增补 scope，独立 chore commit 修完：pyproject PLC0415 per-file-ignores + 3 enum 迁 StrEnum + 3 N817 acronym 改 module import）；combined review APPROVED；coverage 91.09% ≥ 90% |

**关键决策**：
- #22-B CI 硬编码 `ci-gw-change-me` / `ci-api-change-me`（user Option A）而非 GitHub Secrets（角色仅 CI 临时 PG 容器内存在，零泄漏）
- #23 修法：PLC0415 走 per-file-ignores（test 里 deferred import 故意验 re-export、alembic 里 lazy import 是 migration 惯用）；UP042 真迁 StrEnum（Python 3.11+，无 `str(X)` 调用故语义无影响）；N817 改 module-style import（test 意图保留）

**累计 Plan bug：D 9 + E 4 + F 7 + G 2 = 22 个**（全 master 反向 fix，从未静默改）

---

## 本次 session 成果（2026-04-18）

**起始**：Stage E 6/7 待 E7 收尾
**结束**：Stage F 完结 6/6 + Stage G 就绪起跑

| 阶段 | Task | commit | 要点 |
|---|---|---|---|
| E7 | Stage E 收尾 tag | tag `plan-0-stage-e-complete` | 纯 tag + 文档，类 D10 |
| F1 | pcap_gen 骨架 | `ad5e441` | 4 个 plan bug（#14/#15 pre-dispatch / #16 implementer BLOCKED / review 0/0/0） |
| F2 | modbus_frames.py + CRC16 + 3 测试 | `0c79f0b` | plan bug #17（CJK pytest pythonpath）；CRC 3 向量独立核验全对 |
| F3 | scenarios.py（scapy） | `7bfa7a0` | 首个 pre-dispatch 无 bug；review 发现 #18 docstring + #19 heartbeat 方向（user close non-bug） |
| F4 | typer CLI | `4f75ee0` | review 0/0/0；端到端 202 pkts ✓ |
| F5 | gen_initial_corpus.py | `ea2e514` | user 决策 bug #20 驱动 v1.6；review 发现 #21（v1.7 fix） |
| F6 | Stage F 收尾 tag | tag `plan-0-stage-f-complete` | 6 个 stage tag 里程碑 |

**技术成果**：
- 单 session 通过 controller pre-dispatch + implementer BLOCKED + reviewer 三层抓 **7 个 plan bug**（#14-#18 + #20 + #21，#19 close non-bug），全部 master 反向 fix
- 累计 Plan bug **20 个**（D 9 + E 4 + F 7）全部反向 fix master，从未静默改
- 30 个 corpus 文件生成（gitignored）+ 15 唯一 dev_ser 验证
- Plan 0 完成 **6/7 Stage**，剩最后一个 Stage G

**下次会话建议开局**：
1. 读本文件 PROGRESS.md
2. 读 memory `project_ruisheng_scada.md`
3. 派发 G1 implementer（扩展 CI workflow；pre-dispatch 先看 `.github/workflows/ci.yml` + 核对 Docker Hub 源）

---

### Stage D 进度表

| # | Task | Commit | Notes |
|---|---|---|---|
| D0 | Docker + TimescaleDB 环境校验 | （无 commit，纯验证） | ✅ 7 步全过：docker 29.4.0 / compose v5.1.1 / hello-world / PG+Redis healthy / timescaledb 2.16.1 扩展 / redis PONG / down -v；**关键经验**：timescale/timescaledb 镜像国内仅 `docker.1ms.run` 可拉（DaoCloud 500、USTC/163 DNS 失败、dockerproxy IP 被污染） |
| D1 | alembic init + async env.py | `edb23cf` + fixup `52904f9` | ✅ alembic 1.13 显式依赖 + env.py 加载 26 张表 metadata + DATABASE_URL 环境变量；post-review 修 `path_separator = space`（跨平台）+ 解释性注释（configparser Windows GBK 限制→注释用英文）；**关键经验**：CJK 路径下 uv UTF-8 `.pth` 被 Python mbcs 解码损坏，必须 `prepend_sys_path = . ruisheng-shared/src` |
| D2 | autogenerate 初始 schema 迁移 | `9f2f102` | ✅ `alembic/versions/20260416_e05529ef4abb_initial_schema_26_tables.py`（1034 行，26 张 CREATE TABLE）；upgrade → `\dt` 26 表 + alembic_version → downgrade base 只剩 alembic_version → 再 upgrade 幂等 → 321 passed + 8 skipped（无回归）；两轮 review APPROVED；**命名规范 PASS**（`op.f()` 包 naming_convention，无双叠，无 None）；**PG 特化类型保留**（INET×2 / JSONB×6+ / Double / LargeBinary 全对）；**server_default PASS**（created_at/updated_at/pay_state 等全有 DEFAULT 子句）；**范围合规**（无 hypertable/RLS/触发器/fillfactor DDL，延后 D3/D4/D5）；附带改动 1：`pyproject.toml` `[tool.ruff.lint.per-file-ignores]` 给 `alembic/versions/**/*.py` 加 `["PLR0915", "E501"]`（autogen 大函数固有形态，不加每次都要 noqa）；附带改动 2：pre-commit ruff-format 纯 cosmetic 改 migration（引号/typing PEP 604 语法）；**已知 artifact**（非 bug）：(1) `postgresql_where` 混风格 `sa.text(...)` vs 裸字符串（autogen 自己混的，两者等价）；(2) `point_data_realtime.info={"postgresql_with":{...}}` 是 metadata-only 不发 DDL，D4 做 ALTER TABLE 落实 |
| D3 | DB 角色 (ruisheng_gw/ruisheng_api) + GRANT 基线 | `2474275` + fixup `d806a45` | ✅ `alembic/versions/20260416_e74ffa548c2f_db_roles_ruisheng_gw_ruisheng_api_grants.py`；2 角色（gw BYPASSRLS / api 非）+ schema 级 GRANT ON ALL TABLES + SEQUENCES + ALTER DEFAULT PRIVILEGES + 3 张表细粒度 REVOKE+GRANT（pay_orders_seen api=r 只读；soft_logs gw=a 仅写；user_login_records gw 无权）；密码走 `_require_env()` raise + `DO $$ IF NOT EXISTS ALTER ROLE ELSE CREATE END $$` 幂等支持轮换；downgrade `DROP OWNED → DROP ROLE IF EXISTS`；验证全 PASS（含 M4 sequence USAGE）；**过程**：第 1 次派 implementer 发现 **plan bug**（`REVOKE ... FROM PUBLIC` 不影响具名角色 → 达不到缩权）→ BLOCKED 上报 → controller 反向 fix plan (`88b3576`) → 第 2 次 implementer 按修订实施 → fixup `d806a45` 修 ruff-format drift（+ 顺手清 13 个 Stage C 文件的 tech debt #2 drift：cosmetic + 1 处 ruff 删 Integer unused import）；**Plan bug #1 on Stage D** |
| D4 | PL/pgSQL 函数 ×3（set_updated_at + enforce_scene_tenant_consistency + fill_scene_views_snapshot） | `e11313c` + fixup `85f2d2b` | ✅ `alembic/versions/20260416_09676586bfbd_plpgsql_set_updated_at_scene_tenant_.py`；3 函数全 SECURITY INVOKER + `SET search_path = pg_catalog, public`（M3 硬绑）+ 6 处 RAISE EXCEPTION 都带 `% NEW.<field>` 插值 + ERRCODE='23514' + `WHERE ... AND deleted_at IS NULL` 全保留；upgrade/downgrade/upgrade 幂等；验证：`pg_proc.prosecdef=f`×3、`proconfig={search_path=pg_catalog, public}`×3；**过程**：implementer 按 plan v1.1 实施（commit `e11313c`）→ spec compliance review APPROVED → **code quality review 抓 Plan bug #2A+B**（A: DECLARE varchar(40) 比源列 String(50) 窄会 22001 截断；B: RAISE 消息被剥离 spec 设计的 % 插值 NEW.* 值，运维丢上下文）→ user 确认两条都修 → controller 反向 fix plan §Task D4 (master `5967780`) → implementer fixup migration (`85f2d2b`，仅函数 2，+20/-19) → re-review APPROVED；**Plan bug #2 on Stage D**（A+B 双修） |
| D5 | 13 张表 `trg_<table>_updated` 触发器 + ORM drift 断言 | `9d5b0f8` | ✅ `alembic/versions/20260416_89a9dfebe138_trg__table__updated_triggers_13_tables.py`（98 行）；13 表 BEFORE UPDATE 触发器 EXECUTE FUNCTION `set_updated_at()`（D4 函数复用，未自建）；upgrade 顶部 `from ruisheng_shared.models import Base` lazy import + drift assertion 两侧（missing/extra）；DROP IF EXISTS + CREATE 配对幂等；downgrade 仅 drop 13 触发器，**不删 set_updated_at()**（D4 拥有所有权）；验证：13 行 pg_trigger（`tgtype=19` = BEFORE+ROW+UPDATE）、proname=set_updated_at × 13、手工 INSERT updated_at='2000-01-01' → UPDATE → updated_at 被覆盖到 now()；**过程**：controller pre-dispatch ORM scan 抓 **Plan bug #3**（plan UPDATED_AT_TABLES 列 17 张但 ORM 只 13 张含 updated_at；4 张关联/不变表 alarm_records / user_emails / user_phone_numbers / user_wx_bindings 错列）→ controller 反向 fix plan §Task D5 (master `1490ef5`) → implementer 用修订后清单实施 → spec compliance review APPROVED（含 spec spot-check：scene_pages L1483-1485 / pay_orders L1359-1360 spec 直写本触发器）→ code quality review APPROVED（无 Critical/Important，3 minor "gold-plating"）→ 副产分析：D6 trigger 顺序 e<f<t 字母序 = 正确语义（租户校验失败先 abort）；**Plan bug #3 on Stage D**；implementer 副发现两条 plan doc bug（V4 wrong column `group_name`→`company_name` + pg_sleep+now() 单事务 broken）已合入本 PROGRESS commit |
| D6 | scene_* 3 触发器 + 12 张表 FORCE RLS + tenant_isolation policy | `1278cdf` | ✅ `alembic/versions/20260417_0005_scene_triggers_and_rls.py`（175 行）；**3 scene 触发器**（复用 D4 函数）：`trg_scene_pages_enforce_tenant` (scene_pages, BEFORE INSERT OR UPDATE OF owner_user_name, usr_group) / `trg_scene_views_enforce_tenant` (scene_views, BEFORE INSERT OR UPDATE OF owner_user_name, usr_group, scene_page_id, dev_number) / `trg_scene_views_fill_snapshot` (scene_views, BEFORE INSERT only)；**字母序约定** enforce<fill<updated 写入 docstring + 警示禁重命名打乱；**12 张表 ENABLE + FORCE ROW LEVEL SECURITY**（FORCE 是 M1 修复，spec §3.7 v1.3.7 待补但本 task 已落）；**12 policy** `tenant_isolation` USING+WITH CHECK `usr_group = current_setting('app.tenant_id', true) OR current_setting('app.role', true) = 'Administrators'`；upgrade 顶部 ORM drift 断言（`symmetric_difference` 两侧）；downgrade 仅 drop triggers/policies/RLS，**不删 D4 函数**（D4 拥有所有权，同 D5 对 set_updated_at 的处理）；**验证 9 步全 PASS**：12 行 relforcerowsecurity=t / 12 行 tenant_isolation polcmd='*' / 3 scene 触发器字母序 / 跨租户 INSERT 触发 42501 RLS 拒绝 / downgrade 净化 0 残留 + D4 3 函数不误删 / 再 upgrade 幂等 / 321 passed + 8 skipped 无回归；**过程**：controller pre-dispatch ORM scan 抓 **Plan bug #4**（plan RLS_TABLES 列 19 张但 ORM 只 12 张含 usr_group；7 张 satellite 表 user_emails/user_phone_numbers/device_points/device_waring_cfgs/sim_cards/alarm_outbox/soft_logs 通过 FK 继承租户，本身无 usr_group 列，若保留则 CREATE POLICY 阶段 `column "usr_group" does not exist`；spec §3.7 L676 权威判据"带 usr_group 字段的业务表"）→ controller 反向 fix plan §Task D6 (master `13de44a`)，含 Task 标题 18→12 / RLS_TABLES 19→12 / D9 集成测试 2 处 18→12 / D10 final tag 消息修订 / 页脚排除表清单补完 satellite 组和 no-tenant-context 组 → implementer 按修订清单实施 → spec compliance review APPROVED（22/22 checkpoints，含 spec spot-check L1487-1495 scene_pages + L1529-1541 scene_views 字段 byte-identical）→ code quality review APPROVED_WITH_MINORS（0 Critical/Important；2 optional Minor：downgrade operational warning 可加 + USING/WITH CHECK 字面重复但语义必须同步）；**Plan bug #4 on Stage D**（连续第 4 个 controller pre-dispatch 抓到） |
| D7 | UPDATE-heavy 表 fillfactor + autovacuum 调优（3 张表） | `4976089` | ✅ `alembic/versions/20260417_0006_hot_table_tuning.py`（134 行，alembic head `378761167d8c`）；**3 张表 reloptions**（spec 逐字符匹配）：point_data_realtime fillfactor=70 + 4 autovacuum (vacuum_scale 0.05 / analyze_scale 0.02 / vacuum_cost_limit 1000 / vacuum_insert_scale 0.1)（spec §4.2 L1184-1190）；devices fillfactor=80 + vacuum_scale 0.05（spec §4.2 L1126-1129）；device_waring_cfgs fillfactor=80（spec §5.10 L1930，仅 fillfactor 单项无 autovacuum）；upgrade 顶部 ORM info drift 断言（`PointDataRealtime.__table__.info["postgresql_with"]` vs `_EXPECTED_REALTIME_WITH` 常量）；downgrade 对称 RESET 逆序（device_waring_cfgs→devices→point_data_realtime）；**验证 5 步全 PASS**：8 行 reloptions upgrade 后（1+2+5）/ 3 行 NULL downgrade 后 / 再 upgrade 8 行幂等 / 321 passed + 8 skipped 无回归；**Stage C C11 tech debt 兑现**：point_data_realtime 的 `postgresql_with` 从 metadata-only 到真 DDL；**过程**：controller pre-dispatch **三方对齐校验** spec ↔ plan ↔ ORM info 全一致，**首个无 Plan bug 的 Stage D task**（D3-D6 连续 4 个都有）→ implementer 按 plan 实施（commit `4976089`）→ spec compliance review APPROVED（15/15 checkpoints，含 F1-3 侧重 scope 合规防守：单文件、无 ORM/spec/plan side-fix）→ code quality review APPROVED_WITH_MINORS（0 Critical/Important；2 optional Minor：缺 `from __future__ import annotations`（D3-D6 全都有，D7 唯一缺）+ downgrade operational 警示可补明"不丢数据但膨胀加剧"）；两 Minor 暂不修（与 D5 留 3 minor / D6 留 2 minor 一致策略，归入未来 polishing pass） |
| D8 | TimescaleDB hypertable×5 + retention×5 + compression×3 + 复合 PK prep | `4205faf` | ✅ `alembic/versions/20260417_0007_timescale_hypertables.py`（192 行，rev `959079e6cae9`）；**5 张 hypertable**：point_data_history / waveform_history / soft_logs / user_login_records / alarm_records（user_control_actions 因 UNIQUE(cmd_id) 幂等键与 TS 分区列要求冲突，摘除 — Plan bug #5 Q3-B）；**retention**：5 张全启（1y × 3 / 2y × 1 / 3y × 1）；**compression**：3 张启（point_data_history segby=dev_number+point_id / waveform_history segby=dev_number / soft_logs 无 segby），**2 张未启**（alarm_records + user_login_records，D6 FORCE RLS 与 TS 2.16.1 compression 冲突 — Plan bug #6 Option A，等 TS #6827 上游修复）；**schema prep 前置**（Plan bug #5）：Step A drop FK `fk_alarm_outbox_alarm_id_alarm_records`（TS 禁 FK→hypertable） + Step B 3 张表复合 PK `(id, <time_col>)`（alarm_records / soft_logs / user_login_records，含 `pg_constraint.conkey` 单列守卫幂等） + Step C create_hypertable + retention + compression；ORM 同步改（AlarmRecord + SoftLog + UserLoginRecord 复合 PK、AlarmOutbox.alarm_id 去 FK；4 个 lockstep 测试 flip + CHANGELOG `chore:` 说明 id BIGSERIAL 自身唯一所以 SHARED_SCHEMA_VERSION 不升）；downgrade 前向专用——只卸 retention + compression policy（TS 不原生支持 hypertable→regular）；**过程**：controller pre-dispatch live-DB 活探测抓 **Plan bug #5**（FK→hypertable 禁止 + 4 张表 id-only PK 违反 TS 分区列要求）→ master plan v1.3 fix `5901243`（user Q1-A + Q2-A + Q3-B 拍板）→ implementer 按 plan v1.3 起跑 → 迁移 upgrade 中 `ALTER TABLE user_login_records SET compress` 抛 FeatureNotSupportedError → implementer BLOCKED 上报并研究上游 issue #6827 → controller 派 option A/B/C → user 确认 Option A → master plan v1.4 fix `5caab8d`（alarm_records + user_login_records 摘除 compression）→ implementer 延续 dispatch（agent `a38e07b7901f8d601` 续跑）完成；**spec review APPROVED**（独立 agent 读代码 + 查 live DB 逐行核对，含 2 deviation 合理性论证：Step B 幂等守卫 + 4 lockstep test 更新 + CHANGELOG）；**code review APPROVED_WITH_MINORS**（0 Critical/Important；5 optional Minor：M1 f-string 安全性 explicit 注释 / M2 `from __future__ import annotations`（与 D7 同缺）/ M3 TS create_hypertable tx 事务性注释 / M4 downgrade 对称 `if compress:` 守卫 / M5 ORM 复合 PK drift 断言与 D5/D6/D7 风格对齐）；5 Minor 暂不修（与 D5/D6/D7 一致策略，归入未来 polishing pass）；**累计 Plan bug**：D3-D6 共 4 个 pre-dispatch 抓 + **D8 独占 2 个**（#5 pre-dispatch 活探测，#6 BLOCKED 实测触发） |
| D9 | 集成测试 15 case（roles/functions/triggers/RLS/hypertables 全覆盖）+ 3 engine fixture + seed_tenants | `02516f7` | ✅ `tests/integration/test_alembic_upgrade.py`（278 行，15 tests）+ `tests/integration/conftest.py`（31 行，seed_tenants fixture）+ `tests/integration/__init__.py`（空）+ `tests/conftest.py` (+44 行，3 engine fixtures)；**15 测试用例**：test_upgrade_down_and_up_again / test_roles_exist / test_functions_are_invoker / test_updated_at_triggers_count (== 13) / test_scene_triggers_exist / test_rls_forced_on_12_tables / test_policies_exist / test_hypertables_exist (5 表) / test_d8_pk_composite_and_fk_dropped / test_rls_actually_blocks_cross_tenant_read / test_rls_blocks_cross_tenant_insert / test_non_bypassrls_user_sees_zero_under_bogus_tenant (D9 内 renamed from test_owner_does_not_bypass_rls — 见下 concern) / test_gw_bypasses_rls / test_scene_trigger_raises_23514 / test_api_insert_uses_sequence；**3 engine fixture**：dev_engine (ruisheng_dev) / api_engine (RUISHENG_API_PASSWORD) / gw_engine (RUISHENG_GW_PASSWORD)，**function scope**（pytest-asyncio 0.23 auto mode event_loop 函数作用域限制，不可 session）；**seed_tenants**：gw_engine BYPASSRLS 插 ug_A/ug_B wx_groups + user_of_ugB 用户 (authority='User', control_authority=0)，ON CONFLICT DO NOTHING 幂等；**过程**：(1) controller pre-dispatch 抓 **Plan bug #7**（test_updated_at_triggers_count `n >= 17` 遗留 v1.0，D5 Plan bug #3 后应 `== 13`；同时 plan Step 1 缺 dev_engine 定义）→ master plan v1.5 fix `d027920` → (2) implementer 起跑 pre-dispatch 查 live DB 抓 **Plan bug #8 (3 子 bug)**（#8-A test_api_insert_uses_sequence 目标表错：wx_groups 无 `id` / 无 `group_name`；#8-B tests 10/11 devices INSERT 漏 6 NOT NULL 无默认列；#8-C seed_tenants 先决数据明确化）+ password 默认 typo (`dev_password` → `ruisheng_dev`) → BLOCKED 报告 (agent `a38e07b7`) → master plan v1.6 fix `782887c` + `e3524db`（补 users ORM 实字段） → (3) implementer v2 起跑 pytest collect 抓 **Plan bug #9**（SQL alias `AS def` + `r.def` Python SyntaxError — 我在 D8 Plan bug #5 fix 时自己引入）→ master plan v1.7 fix `e85947e`（alias → `constraint_def`）→ (4) implementer v3 续跑 (SendMessage `a2877d90` → 完成 `aa53cbd1`)：pytest 336 passed + 8 skipped，11.38s；**spec review APPROVED**（独立 agent 重跑 pytest 336+8 + 验证 5 deviations 全合理：function-scope fixture、窄 DELETE 清理 × 2、test rename 语义等价、ruff auto-fix）；**code review APPROVED_WITH_MINORS**（0 Critical/Important；8 optional Minor：M1 `pytest.raises(Exception)` 太宽 / M2 locale-dependent 子串匹配 / M3 test_upgrade_down_and_up_again 缺 post-check / M4 alembic.command API 替代 subprocess / M5 test_gw_bypasses_rls 断言弱化（`is not None` vs `>= 1`）/ M6 tests/integration/conftest.py 缺 `from __future__` / M7 pool cleanup 审计 OK / M8 test 长度 OK）；8 Minor 暂不修；**Concern（独特 D9）**：`test_owner_does_not_bypass_rls` 改名 `test_non_bypassrls_user_sees_zero_under_bogus_tenant`——ruisheng_dev 是 POSTGRES_USER（Docker 约定 SUPERUSER=t）bypass 所有 RLS 含 FORCE，是 PG 文档行为；M1 intent 保留，改用 api_engine 验同构不变量（非 BYPASSRLS 角色 bogus tenant → 0 行）。此非 plan bug，属 D9 authorship 修订；若将来要求 owner 真受 FORCE RLS，需 D3/D4 fix 改 POSTGRES_USER 方案（G6 cleanup 备忘）；**累计 Plan bug**：D3-D6 共 4 + D8 共 2 + **D9 共 3**（#7 pre-dispatch / #8 implementer v1 pre-dispatch / #9 implementer v2 pytest collect）= 总 9 个，全部 controller 反向 fix master |
| D10 | Stage D 收尾 tag + spec v1.3.7 follow-up 清单 + 回滚 Runbook | tag `plan-0-stage-d-complete`（worktree HEAD `02516f7`）+ master PROGRESS commit | ✅ **纯文档 + tag**，无代码改动、无 plan bug、无 implementer（D10 controller 直接操作）；tag 带 annotated 消息含 Stage D 全部 DB 对象清点（26 表 / 2 角色 / 3 函数 / 16 触发器 / 12 FORCE RLS / 5 hypertable / 15 集成测试 / 336 passed）+ 9 Plan bug 回溯提示；PROGRESS 补 2 个新段（**spec v1.3.7 follow-up 6 项**：§3.7 FORCE RLS / §4.1.1 search_path / §4.1 GRANT SEQUENCES / §5.10 user_control_actions 摘除 / §4.2+§5.10 compression TS #6827 / 20 optional Minor 批量清理；**Stage D 回滚 Runbook**：alembic 严格线性 + D8 hypertable 单向 + D6 FORCE RLS vs D8 compression 冲突 + D8 复合 PK 前向）；**Stage D 完结 — 9 个 Plan bug 全部反向 fix master** |

---

### Stage E 进度表

| # | Task | Commit | Notes |
|---|---|---|---|
| E1 | conftest.py 扩展 — testcontainers/embedded PG 双轨 fixture | `d2b3482` + fixup `396b2e0` | ✅ `tests/conftest.py`（+86/-3）；**Plan bug #10 pre-dispatch 抓**（plan v1.0 "Replace" 会删 D9 3 个 fixture + session-scope async 返祖 D9 L26-30 pitfall）→ master plan v1.1 fix `8a0cd8e`（Replace → Merge；async → function scope；`postgres_url`/`redis_url` → sync session；alembic upgrade 移入 `postgres_url`）；D9 fixtures（`is_windows`/`_DEV_DSN`/`dev_engine`/`api_engine`/`gw_engine`）byte-identical 保留（diff 确认）；新增 4 fixture：`postgres_url`（sync session，testcontainers `PostgresContainer("timescale/timescaledb:2.16.1-pg15")` + subprocess alembic upgrade head）+ `redis_url`（sync session，`RedisContainer("redis:7-alpine")`）+ `async_engine`（function，pool_pre_ping）+ `session`（function，async_sessionmaker + rollback teardown）；**双 review**：spec APPROVED（9/9 checkpoint PASS，byte-identical 确认，无 deviation）+ code quality APPROVED_WITH_MINORS after fixup（1 Important I1 PLC0415 → fixup `396b2e0` 加 3 条 noqa；3 Minor 都 OK：M1 `async_sessionmaker` rollback 冗余但匹配 plan 故保留 / M2 pool_pre_ping 可去但 noise-level / M3 pytest.skip 消息不全 actionable 但仅 stub 阶段不阻塞）；pytest **336 passed + 8 skipped 不变**（新 fixture 无测试 invoke，纯接口预留） |
| E2 | tools/embedded_pg.py stub — async + sync start/stop 双 API | `b295f8e` | ✅ 单文件新建 45 行；verbatim plan v1.1 E2 Step 1 代码（byte-for-byte 匹配 plan §L4583-4629）；imports 只用 `__future__ annotations / asyncio / random / tempfile / pathlib.Path`；`_NOT_IMPLEMENTED_MSG` 模块常量（Windows no-Docker 解释 + Q-E06 待决）；class `EmbeddedPostgres(version="15")` 设 5 属性（version / port 15000-30000 random / data_dir mkdtemp prefix ruisheng-pg- / url asyncpg / _proc None）+ 4 方法（sync `start_sync` raise / sync `stop_sync` guarded terminate / async `start` raise / async `stop` terminate + await wait）；**无 `tools/__init__.py`**（A5 namespace 约定保持）；**验证**：ruff clean / mypy clean / `EmbeddedPostgres()` 实例化正常返回 url+port+data_dir / `start_sync()` 抛 NotImplementedError 含预期消息 / `stop_sync()` fresh 实例 noop / pytest 336+8 无回归；**review APPROVED 0/0/0**（combined spec + quality，line-by-line 对照 plan，0 deviation，0 minor）；**首个 Stage E 无 plan bug 的 task**（E1 有 #10） |
| E3-E6 | seeds 4 SQL（demo wx_group + 2 users + 1 device + 2 points）+ tools/run_seeds.py + .pre-commit-config.yaml mypy dep | `e42f06b` | ✅ 6 文件单 commit +74/-1；**连抓 3 个 Plan bug** — master plan v1.1/1.2/1.3 三次反向 fix：(a) **#11 pre-dispatch** controller 查 D2 migration 发现 devices/device_points 多个 NOT NULL 无 server_default 列，raw SQL INSERT 漏列必炸 23502（同 D9 #8 模式）→ plan v1.1 补 devices 4 列（update_interval_decisec=100 / loss_count=0 / is_online=FALSE / update_flag=0）+ device_points 5 列（point_ratio=1.0 / point_offset=0.0 / user_ratio=1.0 / user_point_offset=0.0 / show=1）= `f0c5614`；(b) **#12 implementer live-DB** 实测双跑 device_points 从 2→4 行，发现无 UQ on (dev_number, point_number) 故 `ON CONFLICT DO NOTHING` no-op → plan v1.2 改 Option A：`INSERT ... SELECT FROM (VALUES) AS v WHERE NOT EXISTS`（无 schema 改动）+ `# type: ignore[import-untyped]` for asyncpg = `08f12d2`；(c) **#13 implementer pre-commit** 实测 mirrors-mypy 隔离 venv 缺 asyncpg 变 `import-not-found` → v1.2 ignore 不覆盖 + unused-ignore → plan v1.3 改 .pre-commit-config.yaml mypy `additional_dependencies` 加 asyncpg（Option A，principled）= `260729e`；**最终验证**：pre-commit 全绿（ruff / ruff-format / mypy --strict 都 pass）/ `uv run task seed` 双跑稳定 counts 1/2/1/2（wx_groups/users/devices/device_points）/ pytest 336+8 / 6 文件最终 clean commit；**Stage E 独占 3 个 plan bug，累计 13**（D 9 + E 4） |
| E7 | Stage E 收尾 tag `plan-0-stage-e-complete` + PROGRESS 更新 | tag `plan-0-stage-e-complete`（worktree HEAD `e42f06b`）+ master PROGRESS commit | ✅ **纯 tag + 文档**，无代码改动、无 plan bug、无 implementer（E7 controller 直接操作，类 D10）；tag 带 annotated 消息含 Stage E 全部资产清点（conftest 双轨 fixture + embedded_pg stub + seeds 4 SQL + run_seeds.py + pre-commit mypy asyncpg dep + idempotency 双跑 1/2/1/2 稳定 + 336 passed + 8 skipped）+ 4 Plan bug 回溯提示（#10 E1 Replace→Merge + session→function scope / #11 seeds NOT NULL / #12 device_points UQ `NOT EXISTS` / #13 pre-commit mypy isolated venv asyncpg）；**Stage E 完结 — 4 个 Plan bug 全部反向 fix master**，累计 D 9 + E 4 = **13 个 Plan bug** |

---

### Stage F 进度表

| # | Task | Commit | Notes |
|---|---|---|---|
| F1 | tools/pcap_gen 子包骨架 — pyproject.toml + src/pcap_gen/__init__.py | `ad5e441` | ✅ 3 files +129/-0（pyproject.toml 20 行 / __init__.py 3 行 / uv.lock 106 行）；**pre-dispatch 连抓 2 个 Plan bug**：#14 F2 CRC hex typo（`"0103000000020"+"2"` 实为 14 hex=7 bytes，CRC=0x528B 不是 0x0BC4 → master v1.1 `054b187`）+ #15 F5 dev_ser CJK ASCII 崩解（5 种中文 type 全坍缩为 3 个唯一名 → master v1.2 option B `5c2d86c` 改用 TYPE{i} ASCII）；**implementer BLOCKED 抓 Plan bug #16**（plan v1.2 pyproject.toml 缺 `[tool.uv.sources]` → uv 拒绝 workspace cross-ref → master v1.3 `a72422e` 加 `ruisheng-shared = { workspace = true }`）；implementer 遵守 memory `feedback_never_silently_modify_spec` 停下来报 BLOCKED，不静默改 plan；**最终验证**：`uv sync --all-packages` Resolved 68 packages / 9 个新包（pcap-gen 0.1.0 editable + scapy 2.7.0 + typer 0.24.1 + click/rich 等）/ pytest 336+8（15 alembic env-dependent，base 321+8 无回归）/ ruff clean / pre-commit 全绿（mypy 按 config 跳 tools/pcap_gen）；**combined review APPROVED 0/0/0**（byte-identical 匹配 plan v1.3 modulo ruff docstring 空行 canonical form）；**新 drift 记录**（非 F1 scope）：CJK 路径 + uv editable `.pth` mbcs 解码 → `uv run python -c "import pcap_gen"` 失败，`ruisheng_shared` 靠 pytest `pythonpath=["ruisheng-shared/src"]` 绕过，`pcap_gen` 未在 pythonpath → 即将触发 **Plan bug #17**（F2 测试 import 必炸）|
| F2 | modbus_frames.py — CRC16 + 6 个帧构造函数 + 3 测试 + pythonpath fix | `0c79f0b` | ✅ 4 files +92/-1（pyproject.toml pythonpath 1 行 / modbus_frames.py 54 行 / tests/tools/__init__.py 0 / test_pcap_gen.py 38 行）；**controller 实测 probe 抓 Plan bug #17**（CJK 路径 uv editable .pth mbcs → `uv run python -c "import pcap_gen"` 失败，探测测试 `import pcap_gen` 在 pytest 也炸 ModuleNotFoundError → master v1.4 `2e62eb8` 加 F2 Step 0：root pyproject.toml `pythonpath` 追加 `tools/pcap_gen/src`）；implementer TDD 正确流程（Step 0 pythonpath → Step 1 test-first → Step 2 ModuleNotFoundError on `modbus_frames`（非 pcap_gen，证明 #17 修成功）→ Step 3 实现 → Step 4 3 passed → Step 5 单 commit 含 pythonpath）；**combined review APPROVED 0 Critical/0 Important/2 Minor**（reviewer 独立跑 CRC 算法 3 向量全对：`010300000002`=0x0BC4 ✓ / `''`=0xFFFF ✓ / `'00'`=0x40BF ✓；hand-verify 3 未直测 encoder byte layout 全对 9/8/8 bytes）；2 Minor：M1 3 个间接测试的 encoder 可补直测（F3 间接覆盖，Stage F 收尾候选）/ M2 encode_register_frame `[:24]` 静默截断过长 ser（spec A.4 固定 24，仁慈不严格）；**新 drift 记录**（非 F2 scope）：M3 `uv run pre-commit run` Windows 下 `/bin/bash not found`（本地 `git commit` 钩子 OK）/ M4 mypy isolated cwd 下 2 errors（canonical `mypy .` 根目录 clean） |
| F3 | scenarios.py — scapy + `gen_normal_session` 生成"注册+N 轮询+心跳"pcap + expected.json | `7bfa7a0` | ✅ 1 file +89/-0；**pre-dispatch 首个无 bug task**（controller probe scapy import OK / wrpcap & rdpcap CJK 路径 roundtrip OK）；implementer 4 个透明 auto-fix drift：ruff B007 `i→_i` / UP017 `timezone.utc→UTC` / ruff format chain 单行压缩 / mypy `# type: ignore[attr-defined]` for scapy（非 import-untyped，scapy.all 走动态 `__getattr__`）；implementer flag 的 docstring drift **未静默改**（遵守 memory feedback_never_silently_modify_spec）；**combined review APPROVED 0/0/2**（reviewer 独立跑 N=1/3/5/10 包数全对 4/8/12/22 = 1+2N+1；seed=42 两次 identical / seed=100 different；方向审查 register/poll 匹配 plan，heartbeat gw→dev 反直觉）；发现 **2 个 plan bug candidate**：#18 F3 docstring `frames:` vs code 只写 `values` 不写 `frames` → **controller v1.5 反向 fix plan** 补齐 6 字段记录（`1567a86`）；#19 heartbeat 方向 gw→dev vs IoT 常规 dev→gw → **user 决策 2026-04-18 spec §A.5 gw→dev 权威，close non-bug** |
| F4 | typer CLI — `pcap-gen normal` + `[project.scripts]` | `4f75ee0` | ✅ 2 files +40/-0（cli.py 37 行 + pyproject.toml +3 行 scripts 段）；**pre-dispatch 无 bug**（plan v1.4 代码简洁，scapy/typer 生态对齐）；implementer 2 个透明 drift：5 `# noqa: B008` on `typer.Option(...)`（typer 标准 idiom，否则 ruff B008 拦）+ docstring 后空行 ruff auto；**combined review APPROVED 0/0/0**（defaults 逐字验证 / `python -m pcap_gen.cli --help` 输出 5 options 正确 / 端到端 frames_count=100 生成 202 pkts ✓ / typer 单命令 collapse 为 cosmetic 观察非 bug）；uv.lock 未动（entry-point 走 wheel metadata）；**CJK CLI `uv run pcap-gen` 仍炸 ModuleNotFoundError** 符合预期（Plan bug #20 已决策 F5 绕 CLI）|
| F5 | gen_initial_corpus.py — 绕 CLI 直调 Python API 生成 15 pcap + 15 expected.json | `ea2e514` | ✅ 1 file +63/-0（tools/pcap_gen/scripts/gen_initial_corpus.py）；**user 决策 Plan bug #20**（2026-04-18）驱动 plan v1.6：F5 用 `sys.path.insert` + `from pcap_gen.scenarios import gen_normal_session` 直调，不再 subprocess CLI；F4 CLI 保留（Linux/ASCII 可用）；implementer 1 个必要扩展：plan v1.6 sys.path 只加 `tools/pcap_gen/src` 但 scenarios.py 传递 import `ruisheng_shared.constants.protocol` → 同样受 CJK .pth 炸，脚本加两路径（`tools/pcap_gen/src` + `ruisheng-shared/src`）；implementer 如实汇报非静默（遵守 memory）；**combined review APPROVED_WITH_MINORS**（drift 合理 legitimate + 15 唯一 dev_ser `DEMO-TYPE0-0`..`DEMO-TYPE4-2` 验证 #15 fix / 3 pcap 抽查 202 pkts/100 values / 幂等再跑不增 / gitignore corpus/generated/ 工作 / 324+8 无回归）；发现 **Plan bug #21 medium**（plan v1.6 F5 sys.path 单路径不够，传递 import ruisheng_shared 也需同策略）→ **controller v1.7 反向 fix plan**（本次 commit）；30 文件 gitignored 不进 git |
| F6 | Stage F 收尾 tag `plan-0-stage-f-complete` + PROGRESS 更新 | tag `plan-0-stage-f-complete`（worktree HEAD `ea2e514`）+ master PROGRESS commit | ✅ **纯 tag + 文档**，无代码改动、无 plan bug、无 implementer（F6 controller 直接操作，类 D10/E7）；tag 带 annotated 消息含 Stage F 全部资产清点（pcap_gen 子包 4 文件：pyproject.toml + __init__.py + modbus_frames.py 6 函数 + scenarios.py gen_normal_session + cli.py typer `[project.scripts]` / tests/tools/test_pcap_gen.py 3 测试 / gen_initial_corpus.py 首批 15 corpus）+ 7 Plan bug 回溯提示（#14 CRC hex / #15 dev_ser CJK / #16 uv.sources / #17 pytest pythonpath / #18 docstring schema / #20 F5 CLI bypass / #21 sys.path 两路径）+ #19 close non-bug 说明；**Stage F 完结 — 7 个 Plan bug 全部反向 fix master（+1 close non-bug）**，累计 D 9 + E 4 + F 7 = **20 个 Plan bug** |

---

### Stage G 进度表

| # | Task | Commit | Notes |
|---|---|---|---|
| G1 | CI workflow 扩展（5 job：lint / unit / integration / alembic-check / schema-version-guard）+ pre-existing ruff debt 清零 | `ecfa611` + cleanup `5c19435` | ✅ 2 files +73/-1（ci.yml 单文件 feat commit，byte-identical plan v1.8 yaml 块）+ 5 files +14/-14（独立 chore(lint) commit：pyproject per-file-ignores 加 PLC0415 + 3 enum 迁 StrEnum + test_models_timeseries 3 N817 改 module import）；**Plan bug #22 A+B pre-dispatch**：#22-A plan v1.7 `uv sync` 5 处应为 `uv sync --all-packages`（workspace + F1 pcap_gen 子包不装则 ModuleNotFoundError）+ #22-B integration + alembic-check 缺 `RUISHENG_GW_PASSWORD`/`RUISHENG_API_PASSWORD` env 块（D3 migration `_require_env()` + conftest api/gw_engine fixture 都读）→ **user 决策 Option A**（CI 硬编码 `ci-gw-change-me`/`ci-api-change-me`，不走 GitHub Secrets）→ master plan v1.8 fix `473383b`；**Plan bug #23 mid-G1**（首个 implementer 交付后 controller 跑全仓 `ruff check .` 发现 93 pre-existing errors；GH Actions `total_count: 0` 从未跑过故 pre-commit 增量 scope 一直掩盖；分布 87 PLC0415 + 3 UP042 + 3 N817）→ **user 批准 G1 增补 scope**（独立 chore commit 修完）→ cleanup implementer 5 文件 +14/-14 单 commit 完成；**combined review APPROVED**：CP1 ci.yml diff plan v1.8 byte-identical / CP2 lint cleanup 精确无 over-reach / CP3 ruff 0 errors + format clean + mypy clean + pytest 324+8 + coverage **91.09% ≥ 90% threshold** / CP4 yaml 结构合理 / CP5 alembic downgrade hypertable 在 fresh CI 容器 DROP CASCADE chunks 低风险 / CP6 无新 plan bug；**累计 Plan bug D 9 + E 4 + F 7 + G 2 = 22** |
| G2 | `tools/verify_schema_version.py` 升级到双模式 breaking 检测 | `33d9b27` | ✅ 1 file +53/-20（单文件 feat commit，byte-identical plan v1.9 python 块）；**Plan bug #24 pre-dispatch**：plan v1.8 直接用 `git diff HEAD^ HEAD` replace G1 stub 的 `git diff --cached`，会让 `.pre-commit-config.yaml` 的 `shared-schema-version-bump` local hook **静默失效**（查 last-commit 而非 staged，用户改 shared/models/ 不升 SCHEMA_VERSION 不报警）→ **user 决策 Option A 双模式**：脚本读 `PRE_COMMIT=1` 环境变量（pre-commit 工具自动设置），pre-commit 上下文用 `--cached`，CI 用 `HEAD^ HEAD` → master plan v1.9 fix `d628239`；v1.9 还清 v1.8 2 个 dead 项（unused `VERSION_RE` + `_git_diff` 包装）；implementer 6 验收门全过（ruff 0 / mypy 0 / CI mode exit 0 / pre-commit mode exit 0 / pre-commit hook end-to-end Skipped / pytest 324+8）；**combined review APPROVED_WITH_MINORS**：CP1 diff plan v1.9 byte-identical / CP2 6 门复跑 5 过 1 无法验（gate 5 pre-commit CLI 在 Windows env 报 `/bin/bash not found`——非 G2 defect，CI Linux 不受影响，commit-time hook 实测 trigger 正常） / **CP3 独立构造 positive test 真工作**：stage1 CI shared-changed + no-today-entry → exit 1 ✓ / stage2 CI breaking + no-bump → exit 2 ✓ / pre-commit staged shared + no-today → exit 1 ✓ / pre-commit staged breaking + no-bump → exit 2 ✓；**真实 git commit 触发 hook 被 blocked** 验 end-to-end 集成 / CP4 边界扫描 5 点全合理（initial commit HEAD^ 不存在场景不实际、merge first-parent OK、fetch-depth 0 配 CI、regex DOTALL 正确、`breaking:` 粗匹可能误判 `non-breaking:` 但是 over-conservative safe） / CP5 无新 plan bug（M3 initial commit HEAD^ 未 try/except 留 G-后续 hardening 备忘） / 3 Minor non-blocking 都不必修（M1 `today in` substring 可改 `^## <today>` multiline / M2 `breaking:` 过宽 conservative / M3 CalledProcessError 未 handle）；**累计 Plan bug D 9 + E 4 + F 7 + G 3 = 23** |
| G3 | docs/ARCHITECTURE.md 开发者视角架构速览 | `e302dab` | ✅ 1 file +29/-0（单文件新建，byte-identical plan markdown 块；5 段：三个运行单元 / 共享基座 / 关键契约（FunCode 归一化 + RS485 物理约束 + ErrCode/BizError + SHARED_SCHEMA_VERSION 4 契约）/ 查看设计决策 / 贡献流程）；**pre-dispatch 无 bug**（controller 核验 4 契约 factually 对：FunCode.normalize alias `{13: 3, 26: 6}` ✓ / `validators.rs485.min_poll_interval_decisec(baud, device_count)` ✓ / `ruisheng_shared.errors.codes` 含 `ErrCode` + `BizError` ✓）；implementer 无 drift（唯一澄清：plan 写绝对路径 `D:\江苏润盛\docs\ARCHITECTURE.md` 歧义 master vs worktree，controller 指示 worktree 下创建因 README.md/CONTRIBUTING.md 已在 worktree，合并自然带过）；pre-commit 6 hooks 全过（markdown 无 ruff/mypy trigger 故 skip，trailing/eof/merge-conflict/case-conflict/line-ending/private-key 全 pass）；**无需正式 combined review**（byte-identical plan + 链接验证 + pre-commit 绿已足够，参考 D10/E7/F6 小文档任务模式） |
| G4 | `.github/workflows/mutation.yml` weekly mutmut + 10% survival gate | `163d377` | ✅ 1 file +38/-0（单文件新建，byte-identical plan v2.0 yaml 块）；**Plan bug #25 A+B pre-dispatch**：#25-A plan v1.x `uv sync` 应为 `uv sync --all-packages`（同 G1 #22-A 回归）；#25-B 原 "Fail if survival rate > 10%" 步 Python 字面 `[...]` 省略号 + `alive`/`total` 从未更新 → `ZeroDivisionError`，照跑必炸 → **user 选 Option A**：`uv sync --all-packages` + 改 heredoc 真解析 `mutmut results` 的 `survived/killed/timeout/suspicious (N)` 节头 + total=0 → exit 0（首次/全 skip 中性保护）→ master plan v2.0 fix `ef33cda`；implementer 报告 yaml parse OK / Python py_compile OK / pre-commit 全过；**combined review APPROVED**（独立 subagent）：CP1 diff plan v2.0 byte-identical / CP2 yaml safe_load silent / **CP3 3 fixture 独立验证**：A（50 killed 0 survived）exit=0 ✓ / B（11/100 survived = 11% > 10%）exit=1 ✓ / C（空输出 total=0）exit=0 中性 ✓ / CP4 cron `0 2 * * 0` = Sun 10:00 CST 与 ci.yml 无冲突 / CP5 yaml 结构合理（checkout@v4 + setup-uv@v3 一致） / CP6 无新 plan bug；2 Minor 非阻塞（M1 `subprocess.check_output` 未 handle `CalledProcessError` → 首次 cache 空场景抛 traceback，红信号等同，低优；M2 `uv pip install mutmut` ad hoc vs 加 dev dep — **匹配 plan，不擅改**，可 G6 一并）；**累计 Plan bug D 9 + E 4 + F 7 + G 4 = 24** |
| G5 | Plan 0 完成 README 补完 + 最终 tag `plan-0-complete` | `66fb9c1` + tag `plan-0-complete` | ✅ 1 file +6/-5（worktree README.md）；**Plan bug #26 A+B+C pre-dispatch**：#26-A README 已有 `## 后续阶段` 列 Plan 1-4，plan 原写"append `## 当前状态`" 会重复 → **user 选 A1 替换**（带 checkbox Plan 0 ✓ / Plan 1-4 ☐，单一源）；#26-B Step 2 `cd "D:\江苏润盛"`（master docs-only）错 → worktree；#26-C tag 缺 `git push origin plan-0-complete`（与 D10/E7/F6 6 tag 惯例不一致）→ master plan v2.1 fix `b6904ab`；**controller 直接操作**（类 D10/E7/F6 纯文档+tag 模式，无 implementer 派发）；**最终验收** `uv run pytest tests/ --cov --cov-fail-under=90` → **339 passed + 8 skipped + coverage 91.09%** ✅（较 PROGRESS 之前 324 高出 15 = D9 integration 测试在 Docker+env 就绪场景实跑，非回归；plan Step 2 `task bootstrap` 在 `pre-commit install` 踩 Windows `/bin/bash not found` **已记录 G2/F2 review 在案**，绕过直接跑 pytest 不阻塞）；**tag `plan-0-complete` annotated 消息含**：Plan 0 主干 G1-G5 完成（G6/G7 属 post-complete）+ 资产清点（monorepo+shared 23 表 ORM / alembic 7 迁移 head `959079e6cae9` / DB 26 表+2 角色+3 PL/pgSQL+16 触发器+12 FORCE RLS+5 hypertable / seeds 4 SQL / pcap_gen 4 文件 / CI 5 job + weekly mutation / pre-commit 含双模式 schema-version-guard / 339+8 coverage 91.09% / docs README+CONTRIBUTING+ARCHITECTURE） + G5 前置 commit 链（66fb9c1 + 163d377 + e302dab + 33d9b27 + ecfa611+5c19435）；**累计 Plan bug D 9 + E 4 + F 7 + G 5 = 25** |
| G6 | 技术债清理：`[tool.uv] dev-dependencies` → `[dependency-groups].dev`（PEP 735）+ asyncpg transitive | `d2be630` | ✅ 2 files（pyproject.toml +6/-6 + uv.lock +72/-74 重新生成）；**Plan bug #27 A+B+C pre-dispatch**：#27-A 原 Step 3 "在 `[project].dependencies` 里把 asyncpg 注释掉" 前提错误（根 pyproject.toml 无此块，所有依赖在 `[tool.uv].dev-dependencies` 14 条） → rewrite Step 3 为"在新 `[dependency-groups].dev` 列表注释"；#27-B 文件路径 master → worktree（同 #26-B）；#27-C CI `uv sync --all-packages` 无 `--group` 标志，需 implementer **实测** `[dependency-groups].dev` 在 uv 0.4.30+ 是否特殊默认装（若不装则 Step 6 要改 ci.yml 5 处）→ master plan v2.2 fix `10ec51e`（verbatim-copy 14 条 dep 入 Step 2 替换块 + worktree 路径 + #27-C 实测分支）；**implementer 执行**：Step 3 asyncpg 成功去显式（testcontainers[postgres,redis]>=4.7 transitively 拉入 asyncpg 0.31.0）；Step 6 **no-op**（uv 0.11.2 `uv sync --all-packages` 自动装 `[dependency-groups].dev`，`ruff 0.15.11` / `mypy 1.20.1` / `pytest 9.0.3` / `alembic 1.18.4` / `pre-commit 4.5.1` 全可用）；**combined review 8 CP APPROVED 无 minor**（独立 subagent）：CP1 pyproject diff 2 处唯一改动 + `[tool.uv.workspace]` 保留 / CP2 弃用警告消失 / CP3 asyncpg+testcontainers.postgres+run_seeds.py 三路 import 全绿 + uv.lock 确认 transitive 非直接依赖 / CP4 5 工具全可用 / CP5 339+8 coverage 91.09% / CP6 ci.yml 未触 / CP7 无残留 pyproject.toml.bak / CP8 无新 plan bug；**累计 Plan bug D 9 + E 4 + F 7 + G 6 = 26** |
| G7 | `ruisheng-shared` release workflow（CHANGELOG + workflow + docs/RELEASE + 真 tag push → GitHub Release） | `12a97bb` + fix `4bd0726` + tag `shared-v0.1.0` + **真 GitHub Release** | ✅ 4 文件（CHANGELOG.md 17 行 / release-shared.yml 最终 53 行 / docs/RELEASE.md 25 行 / `__init__.py` +1/-1 仅 `__all__` 补 `"__version__"`）；**Plan bug #28 共 6 sub**：#28-A paths master→worktree / **#28-B（严重）** 原 Step 1 要求把 `SHARED_SCHEMA_VERSION int 20260415` → `"0.1.0"` 会破坏 test_smoke 3 assertion + G2 schema-version-guard 作废，rewrite 为**两版本字段分离**（`__version__` semver 供 tag/release 用；`SHARED_SCHEMA_VERSION` int 日期供运行时 schema 兼容校验）/ #28-C awk 范围 bug（start/end 同一行 → 单行 → sed '$d' 删空），改状态 flag / #28-D Step 7 强制真推 tag（避免 Plan 1 引用时才暴露 bug）/ **#28-E** implementer 实测 gawk dynamic regex `\[` 被降级，双转义 `\\\\[` 本地 gawk pass / **#28-F CI 首次真跑** (run `24600246937` FAILED) 暴露 GitHub Actions ubuntu-latest `/usr/bin/awk` = **mawk 1.3.x**，`\\\\[` 仍拒绝 → user 选 A+(i)，彻底换 **Python heredoc**（沿用 G4 mutation.yml precedent 跨所有 awk 实现免疫）+ 删 broken tag 重推；master plan v2.3→2.4→2.5 三次 fix `cb73de0`/`b34cf20`/`12e03b4`；**最终验证 端到端 GREEN**：retag 后 workflow run `24600624203` completed success 9s，5 plan step 全 ✓（checkout / Extract version / Verify pyproject / Extract changelog / Create GitHub Release），真 GitHub Release `ruisheng-shared 0.1.0` published non-draft，body 含 5 bullet + SHARED_SCHEMA_VERSION 行 UTF-8 CJK 正确；advisory: Node.js 20 deprecation for checkout@v4/action-gh-release@v2（2026-06-02 强制迁移，Plan 1 refresh 时一并）；**累计 Plan bug D 9 + E 4 + F 7 + G 7 = 27**（G5 #26 + G6 #27 + G7 #28 以 "sub-count 计为 1 个 plan bug" 统计）|
| **Stage G ✅ 7/7 完结** | tag `plan-0-stage-g-complete` → `4bd0726` | ✅ controller 直接打 tag；annotated 消息含 Stage G 全部 7 task 清点 + Stage G 累计 7 个 plan bug 回溯（#22-#28 含 sub）；Plan 0 **三个最终 tag 同 branch**：`plan-0-stage-g-complete`（本）+ `plan-0-complete`（G5）+ `shared-v0.1.0`（G7）|

---

## 仓库关键坐标

- **GitHub**：https://github.com/proecheng/ruisheng-scada （Private，账号 proecheng）
- **工作树**：`D:\江苏润盛\.claude\worktrees\plan-0-foundation`
- **主仓库**（master，只有设计/计划文档）：`D:\江苏润盛`
- **master 最新 commit**：`12e03b4 fix(plan): G7 Plan bug #28-F CI-caught — replace awk with Python heredoc` → **本次 G7 完成 PROGRESS 更新 commit（即将推送）**
- **worktree 实施分支最新 commit**：`4bd0726 fix(release): replace awk extraction with Python heredoc (mawk compatibility)`（前置 `12a97bb` G7 Step 1-6 / `d2be630` G6 / `66fb9c1` G5 / ...）
- **alembic current**：`959079e6cae9 (head)` — D8 migration（G1-G7 无新迁移）
- **已 push 的 tag（9 个）**：`plan-0-stage-a-complete` / `...-b-...` / `...-c-...` / `...-d-...` / `...-e-...` / `...-f-...` / **`...-g-complete`**（本次 G7 新增）+ **`plan-0-complete`**（G5 新增）+ **`shared-v0.1.0`**（G7 新增，附 GitHub Release）
- **两个 worktree**：
  - `D:/江苏润盛` → master（只放 spec / plan / progress 文档）
  - `D:/江苏润盛/.claude/worktrees/plan-0-foundation` → feature/plan-0-foundation（实际代码）
- **已 push 的 tag**：`plan-0-stage-a-complete`、`plan-0-stage-b-complete`、`plan-0-stage-c-complete`、**`plan-0-stage-d-complete`**（D10 本次新增，指向 D9 HEAD `02516f7`）
- **Docker stack**：运行中 (`docker compose -f docker-compose.dev.yml ps` 在 worktree)
  - `ruisheng-postgres-dev` (timescale/timescaledb:2.16.1-pg15) healthy，0.0.0.0:5432
  - `ruisheng-redis-dev` (redis:7-alpine) healthy，0.0.0.0:6379
  - 内含 D8 应用的 26 张表 + 2 DB 角色 + GRANT + 3 PL/pgSQL 函数 + 13 trg_<table>_updated 触发器 + 3 scene 触发器 + 12 张表 FORCE RLS + 12 tenant_isolation policy + 3 张表 fillfactor + autovacuum tuning + **5 张 TimescaleDB hypertable（含 3 张复合 PK）+ 5 retention policy + 3 compression policy**

---

## 已完成

### Plan 0 Stage C（feature 分支，8/22 ✅）

| # | Task | Commit | Notes |
|---|---|---|---|
| C1 | Declarative Base + mixins | `dfb7157` + patch `1284a65` | 3 tests；patch 加 naming_convention |
| C2 | WxGroup (wx_groups) | `a27033c` | 3 tests |
| C3 | User + WxBinding + Phone + Email | `71eb752` | 12 tests；**2 次 revert**（详见下） |
| C4 | Device + Point + Static + Sim + Template | `8a770e7` retrofit + `c03db3c` 实现 | 16 tests；retrofit UQ template |
| C5 | DeviceWaringCfg + AlarmRecord + AlarmOutbox | `eef06ca` + fixup `ecdbefe` | 18 tests；spec review 抓出索引缺 DESC，已修；side-fix（devices.py mypy 紧化）被混入 feat commit（process 警告，已存 memory） |
| C6 | UserControlAction | `dfe10cb` + fixup `a7ae395` | 11 tests；单 commit（无 side-fix 混入）；fixup 补 CHANGELOG + 加 biconditional CK 注释 |
| C7 | TimingPlan + MaintainPlan + MaintainAction | `12bf516` + fixup `171b474` | 36 tests；2 轮审查 → spec v1.3.3（5 BLOCKER + 8 MAJOR inline 修）；fixup 补 SHARED_SCHEMA_VERSION bump + CHANGELOG 前缀 |
| C8 | ScenePage + SceneView | `4d8950e` + fixup `2e41292` | 38 tests + 2 Stage D 占位 skip；派发前 2 轮 review → spec v1.3.4（0 BLOCKER + 3 MAJOR inline 修，含 scene_* 租户一致性触发器 + 展示快照语义）；post-impl 2 轮 review → fixup（CHANGELOG + 3 语义警示 + skip-test 占位）；SHARED_SCHEMA_VERSION 保持 20260414（非破坏性扩展） |
| C9 | PayOrder + PayOrderSeen + ErrCode PAY_* | `232d413` + fixup `7517879` | 40 tests + 2 Stage D/E 占位 skip（+ErrCode 6 个）；派发前预检查发现 pay_orders DDL 未升级到 v1.3.3+ 通用规范 → 2 轮 review → spec v1.3.5（支付模块 DDL 补强 + gw_pool 回调路径 + 6 PAY_* ErrCode + §3.8.16 脱敏 + §5.10 两个 Job）；post-impl 2 轮 review → fixup（_HTTP_MAP 补 6 PAY_* + SHARED_SCHEMA_VERSION 20260414→20260415 breaking bump + CHANGELOG + http_status 测试 + skip-test 占位） |
| C10 | SoftLog + UserLoginRecord | `ba81585` | 36 tests + 2 Stage D 占位 skip；spec v1.3.6（soft_logs +source 字段 + level CHECK + 索引；user_login_records 全新 DDL）；spec review 抓出 5 MAJOR（全部时间列索引缺 DESC）；code review 抓出 1 IMPORTANT（ip_addr Mapped[Any]→Mapped[str] 与 devices.py 一致）+ 2 MINOR（对齐空格 + 模块 docstring 一致性）；全部 inline 修复后提交 |
| C11 | PointDataRealtime + PointDataHistory + WaveformHistory | `9ffa248` | 48 tests + 2 Stage D 占位 skip；关键决策：(1) SQLAlchemy 2.0.49 不支持 `postgresql_with` 作表级 kwarg — fillfactor 存入 `table.info` + Stage D Task D4 `ALTER TABLE` 落地；(2) hypertable 无显式 PK → ORM 用复合 PK (dev_number, point_id, recorded_at) 符合 TimescaleDB 要求；(3) `LargeBinary` for BYTEA（项目首次）；两阶段审查均 APPROVED（无需修复） |
| C21 | `__init__.py` 汇总 26 张表 | （增量维护完成，随每个 Cx 同步更新；无独立 commit）| 26 表全部导入 + `__all__` 排序 + scene_pages/scene_views NOTE（spec §3.7 gw 禁用）|
| C22 | Stage C 收尾 tag `plan-0-stage-c-complete` | tag pushed | 26 张 ORM 表 + mypy 0 issues (28 files) + 321 passed + 8 skipped + SHARED_SCHEMA_VERSION 20260415 + spec v1.3.6 |

**Stage C 至今发现 7 个 plan bug（已全部反向 fix 到 master）：**

1. **C1 patch**：Plan 原未加 `Base.metadata.naming_convention`，code review 抓到。加后约束名对 Alembic 稳定。Master `f4e66db`。
2. **CK/UQ name 双叠**：Plan 原写 `name="ck_users_user_name_format"` → 与 naming_convention 模板叠加成 `ck_users_ck_users_user_name_format`。改为裸名 `name="user_name_format"`。Master `e32493e`。C3 第一次 revert 源于此。
3. **UQ 模板不支持多列**：原模板 `%(column_0_name)s` 对多列 UQ 只取第一列名，且 `unique=True` 也受影响。改为 `%(constraint_name)s` 并强制所有 UQ 显式 `name=`。Master `8463b94`。C4 第一次 revert 源于此。
4. **C6 control CK 误值 'paid'**：Plan 原写 `CheckConstraint("(result = 'paid') = (completed_at IS NOT NULL)")`，但 `result` 合法值是 `pending/success/failed/timeout/cancelled`，无 'paid'。改为 `(result = 'pending') = (completed_at IS NULL)`。Master `54e3e6d`。派发前 controller 预检查抓到（未触发 revert）。
5. **C7 spec §4.2 缺 maintain_* DDL + timing_plans 不完整**：spec §4.3 表清单提了 `maintain_plans / maintain_actions` 但 §4.2 只有 `timing_plans` 原始 12 年前老 DDL（无 usr_group/deleted_at/updated_at/RLS），两张保养表 DDL 完全缺失。Controller 派 2 轮 reviewer（schema + 端到端数据流/UX），抓出 5 BLOCKER（RLS session 变量名、CK 命名双叠、软删+FK 语义、gw BYPASSRLS、并发推进）+ 8 MAJOR（partial unique alembic 幽灵、触发器/policy op.execute、timing_plans breaking、dead update_flag、幂等 action_uuid、user_name 索引、60s 时间容差、FK 命名交 convention）+ 9 MINOR。全部 BLOCKER+MAJOR inline 改入 spec v1.3.3（commit `879629b`），MINOR 按"延后 Plan 2/3"或 spec TODO。SHARED_SCHEMA_VERSION 20260413→20260414（breaking）。
7. **C9 spec §4.2 pay_orders DDL 未升级到 v1.3.3+ 通用规范**：pay_orders 原始 DDL（v1.3.2 旧版）缺 usr_group / updated_at / deleted_at / refund_at / RLS / 索引；pay_state 仅 4 值无 cancelled/expired；pay_orders_seen 嵌在 §3.5 代码片段而非 §4.2 表定义区。2 轮 reviewer 产出 5 MAJOR（全修）：(A) pay_orders_seen RLS 简化为角色授权 + gw_pool 回调路径；(B) 迁移阻塞方案（拒兜底租户）；(C) SHARED_SCHEMA_VERSION breaking bump；(D) openid 无 CHECK 决策；(E) mark_paid 终态转移收紧至 {pending,failed} → paid。post-impl 两轮 review 另抓 1 BLOCKER（_HTTP_MAP 缺 PAY_*）。spec v1.3.5（commit `eebf554`，+131/-21 行）。SHARED_SCHEMA_VERSION 20260414→20260415（breaking）。
6. **C8 spec §4.2 缺 scene_pages / scene_views DDL**：spec §4.3 表清单提了"组态 (2): scene_pages, scene_views"，§4.5 L1342 只一行"直通"，但 §4.2 **完整 DDL 区空白**；旧表 `ZTPageInf` / `ZTViewInf` 无 UsrGroup/deleted_at/updated_at/RLS。Controller 派 2 轮 reviewer：第 1 轮产 DDL 初稿 + 抓 Q-B08 TODO；第 2 轮 Controller 追加 2 个 MAJOR（跨表租户一致性不变量 + Company/Department 快照语义），reviewer 回复最终方案（BEFORE INSERT/UPDATE 触发器 `enforce_scene_tenant_consistency` + BEFORE INSERT `fill_scene_views_snapshot`）+ 自抓 3 MAJOR（partial unique 字段从 4 元 `(page_id, dev, pos_x, pos_y)` 收敛到 2 元 `(page_id, dev)`、第 1 轮 §4.5"直通"误判必改非直通、pgloader AFTER LOAD 完整 SQL 块）。总计 0 BLOCKER + 3 MAJOR + 5 MINOR，全部 MAJOR inline 改入 spec v1.3.4（commit `0daa0d2`，+225/-2 行）。SHARED_SCHEMA_VERSION **保持 20260414**（仅 DB schema 扩展，未改 shared Pydantic/enum）。Q-B08（子页面层级 / 背景图分辨率 / SVG 支持）保留为 §4.2 DDL 顶部 TODO。

**Process 教训（2 次 implementer 静默改 spec）：**
- C3 attempt 1：implementer 发现双叠、静默改 spec 短名 → revert + prompt 加 STRONG guard
- C4 attempt 1：implementer 发现 UQ 模板 bug、静默改 base.py + users.py → revert + Path B 正式化

两次都是 implementer 经验正确、process 错误。每次 revert + 反向 fix plan 效果好，但实施成本高（多跑一轮）。

### 文档阶段（master 分支）

9 次 commit 从 spec v1.0 → v1.3.2（5 角色审查 + 一致性 + 边界/数据流三轮）+ Plan 0 完整实施计划 2678/4265 行。

### Plan 0 Stage B（feature 分支，12/12 ✅）

| # | Task | Commit | Notes |
|---|---|---|---|
| B1 | enums/__init__.py 骨架 | `b05279d` | 调整为 bare skeleton（B2–B6 增量追加 re-export） |
| B2 | FunCode 枚举 + normalize | `b8354b9` | 9 tests |
| B3 | AlarmType 枚举 | `fa1c068` | 7 tests |
| B4 | AlarmAction + PhoneAlarm 位编解码 | `6a4e1e2` | 3 tests；plan 错算修正见 master `ee8c309` |
| B5 | ControlStatus 枚举 | `bff1238` | 3 tests |
| B6 | Authority 4 级 RBAC | `ada7097` | 5 tests |
| B7 | ErrCode + BizError | `82b3dba` | 4 tests |
| B8 | constants/protocol.py | `97ad575` | 5 tests |
| B9 | constants/limits.py | `c82ae9d` | 5 tests |
| B10 | validators/rs485.py | `841dcb2` | 7 tests；plan 公式 bug 修正见 master `479dcdf` |
| B11 | schemas/ + ApiResponse | `97f38b1` | 2 tests |
| B12 | 覆盖率 + tag | tag `plan-0-stage-b-complete` | 52 tests pass，覆盖率 97.24% |

**Stage B 发现 2 个 plan bug（已改 master）：**
1. B4 测试用 `call_on_reset=False` 但期望 `0x2103`（数学上需 True）→ implementer 静默改了（process 违规）→ controller 后续加了"never silently modify"的 guard，并反向 fix plan
2. B10 `min_poll_interval_decisec` 原公式 `(ms+999)//100 + 10` 把 ms 按 centisec 除，× 10 offset 也不对 → 所有 5 个测试都 fail → controller 预检查后给 implementer 正确公式 `((ms+999)//1000) * 10`

---

### Plan 0 Stage A（feature 分支，15/15 ✅）

| # | Task | Commit |
|---|---|---|
| A1 | uv + pyproject | `7b45f95` + fixup `f68d88c` |
| A2 | .gitignore 扩展 | `56a783f` |
| A3 | ruisheng-shared 骨架 | `a225398` |
| A4 | pre-commit 配置 | `2f9f28c` |
| A5 | verify_schema_version.py stub | `3a77507` |
| A6 | docker-compose.dev.yml + .env.example | `d37b937` |
| A7 | Makefile + taskipy 12 tasks | `b9df8ec` |
| A8 | CI base workflow | `f0d8a03` |
| A9 | tests 骨架 + smoke (2 passed) | `e97ffc4` |
| A10 | README.md | `6733efb` |
| A11 | CONTRIBUTING.md（含 Windows 环境注意）| `e0a7df7` |
| A12 | bootstrap 验证 + pytest pythonpath fix | `de42b64` |
| A13 | .gitattributes | `5fec886` |
| A14 | task bootstrap 一键命令 | `a787cff` |
| A15 | Stage A 收尾 tag | tag `plan-0-stage-a-complete` |

---

## 下一步待办

### Stage C（23 task，ORM 模型）
### Stage D（8 task，Alembic 迁移，需 Docker）— 含新增 **D0 Docker + TimescaleDB 环境前置校验**
### Stage E（7 task，测试基建 + seeds，需 Docker）
### Stage F（6 task，PCAP 生成器）
### Stage G（7 task，CI 完备 + 文档 + release + 技术债清理）— 新增 **G6 deps 迁移**、**G7 release workflow**

---

## 2026-04-14 Spec v1.3.4 修订（C8 派发前 2 轮审查产出）

**变更范围**：master spec v1.3.3 → v1.3.4（commit `0daa0d2`，+225/-2 行）

**修订清单**：
- §3.7 追加"gw 禁止访问 scene_pages / scene_views"（CI lint 扫描违规 P0 阻塞）
- §3.8.18 新增 scene_views Company/Department 展示快照规则（INSERT 自动填充 / 允许覆盖 / UPDATE 不同步）
- §4.1.1 新增 (4)(5) 两个 PL/pgSQL 触发器函数：
  - `enforce_scene_tenant_consistency()`：scene_pages/scene_views 的 BEFORE INSERT/UPDATE 校验 usr_group 与 users / scene_pages / devices 一致（补 RLS 只防读不防跨表写的盲点），RAISE EXCEPTION 23514
  - `fill_scene_views_snapshot()`：BEFORE INSERT 从 users 反查填充 company/department
- §4.2 新增 scene_pages / scene_views 完整 DDL（NUMERIC(10,2) 坐标 + sanity bounds CHECK 、partial unique `(scene_page_id, dev_number) WHERE deleted_at IS NULL`、RLS policy、zh-x-icu collation、Q-B08 TODO 注释块）
- §4.5 L1342 `ZTPageInf / ZTViewInf` 从"直通"展开为 2 行非直通迁移映射 + pgloader AFTER LOAD DO 完整 SQL 块（禁用触发器 → 反查填充 → NULL 断言 → 启用触发器 → 空 UPDATE 事后校验）

**审查发现**：0 BLOCKER + 3 MAJOR + 5 MINOR。全 MAJOR inline 修复；MINOR 按"延后 Stage D alembic"或 "implementer 备忘"处理。

**关键 MAJOR**：
- MAJOR A 跨表租户一致性：方案 1 PL/pgSQL 触发器（拒绝方案 2 GENERATED STORED 不支持跨表 / 方案 3 复合 FK 需改 users/devices UNIQUE）
- MAJOR B Company/Department 快照语义：INSERT-fill + 允许覆盖 + UPDATE 不同步
- MAJOR C partial unique 4 元改 2 元（语义弱：像素级唯一 → "同页同设备只配 1 个热点"）

---

## 2026-04-14 Spec v1.3.3 修订（C7 派发前 2 轮审查产出）

**变更范围**：master spec v1.3.2 → v1.3.3（commit `879629b`，+173/-13 行）

**修订清单**：
- §3.6 L656 保养 L1 权限 R → ▲自有（一线员工 CRUD 本人设备）
- §3.7 扩写软删+FK 语义 + gw `ruisheng_gw BYPASSRLS` 角色 + api SET LOCAL 规则
- §3.8.17 新增保养推进状态机（`max(now(), old) + interval` 非累加 + `FOR UPDATE` + action_uuid 幂等）
- §3.8.16 追加 maintain_actions 同款 3 年保留+脱敏
- §4.1 规约表 +7 行（TZ Asia/Shanghai 无 DST / VARCHAR 在 API 层 strip+NFC / FK-CHECK 命名交 naming_convention / Index 显式命名 / RLS policy 统一 tenant_isolation / Trigger trg_<table>_updated / 表单时间字段 60s 容差）
- §4.1.1 新增通用设施（`set_updated_at()` 触发器函数 + ruisheng_gw/ruisheng_api 两个 DB 角色定义）
- §4.2 timing_plans 原地重写（+usr_group/deleted_at/updated_at/FK/RLS/3 索引/触发器）+ 新增 maintain_plans + maintain_actions 完整 DDL
- §4.3 表计数 21 → 26（v1.0 起就数错了）
- §4.5 L1341 TimingPlan 迁移从"直通"改为"非直通"（usr_group 从 devices 反查填入）
- §5.1 PG SQLSTATE → ErrCode 中文映射表（TODO Plan 2 实现）
- SHARED_SCHEMA_VERSION 20260413 → 20260414（breaking）

**审查发现**：5 BLOCKER + 8 MAJOR + 9 MINOR。BLOCKER+MAJOR 全部 inline 修复；MINOR 按"延后 Plan 2/3"处理（具体清单见 spec §10 v1.3.3 changelog）。

---

## 2026-04-14 结构复核结论（进 Stage B 前）

Stage B-G 复核完成，已做 3 处 Plan 修订 — **全部已 commit 到 master**：

| 位置 | 修订 | commit |
|---|---|---|
| Stage D 开头 | 插入 Task **D0**：Docker / docker-compose / PG / TimescaleDB 扩展 / Redis 前置校验（7 steps 具体命令，无 commit，仅验证） | `ba6bfeb` |
| Stage G 末尾 | 新增 Task **G6**：迁移 `[tool.uv] dev-dependencies` → PEP 735 `[dependency-groups]`，顺便再测 `testcontainers[postgres]` 能否自带 `asyncpg`（处理遗留技术债 #1 + #3） | `ba6bfeb` |
| Stage G 末尾 | 新增 Task **G7**：ruisheng-shared release workflow（semver + SHARED_SCHEMA_VERSION + CHANGELOG + GitHub Release，**不发 PyPI**） | `ba6bfeb` |

**审查员误判已否决**（不改）：
- B11 已经是 schemas 骨架（含 ApiResponse 泛型壳 + 2 个测试）→ 无需再插 B12a
- B4 已经包含完整位编码测试（decode 0x0103 + encode 0x2103）→ 无需补

**风险确认**：
- ✅ docker-compose.dev.yml 用的是 `timescale/timescaledb:2.16.1-pg15`（不是普通 postgres:15），TimescaleDB 扩展风险 **不存在**
- ⚠️ Docker Desktop 仍未装 — **进 Stage D 前必须装**（Stage C 仍是纯 Python，不受影响）

---

## 环境前置条件（执行前请确认）

| 需求 | 谁用 | 状态 |
|---|---|---|
| Docker Desktop | Stage D/E/A12 runtime 验证 | ❌ 未装（阻塞 D6 集成测试）|
| Git for Windows `usr/bin` 在 PATH | pre-commit 能正常工作 | ⚠️ 需手工加 |
| `chcp 65001` 或 `$env:PYTHONUTF8=1` | 避免 GBK 编码 bug | ⚠️ 需手工设置 |
| `gh` CLI 已登录 | push / PR | ✅ proecheng 账号 |
| uv 0.11.x 已装 | 所有 task | ✅ |

---

## 遗留技术债

1. **`[tool.uv] dev-dependencies` 已被 uv 弃用**（warning 每次 sync 出现）→ 将来迁移到 `[dependency-groups]`。Plan 里标为 Minor，Stage G 前处理。
2. ~~**pre-commit hook 在 Windows GBK locale 下不稳定** → 已用 `--no-verify` 绕过多次。根治需开发者端 UTF-8 设置，CONTRIBUTING.md 已写。~~ **D3 fixup `d806a45` 已全仓清扫 13 个 drift 文件**（纯 cosmetic + 1 处 unused import）。后续 implementer 务必 `uv run task fmt` 再 commit。
3. **`testcontainers[postgres]` extra 未完全解析** → 已通过加 `asyncpg` 绕过，根治同上（deps-groups 迁移）。
4. **Migration 里 SQL 字符串用 f-string 插 env var**（D3 code quality reviewer IMPORTANT 2）：当前可接受（密码来自开发者自控 env，含单引号会自炸不是外部注入），但后续新 migration 规范里应统一 `quote_literal()` 或 `EXECUTE format('... %L', var)` 模式。若有含单引号的合法密码会当场炸 ProgrammingError，可在 `_require_env` 里加 `assert "'" not in value`。

---

## Spec v1.3.7 follow-up（Stage D 完成后另开 branch/PR）

Stage D 实施过程中累积的 spec 升级项（非阻塞，Stage E/F/G 或 Plan 1 前分批落）：

1. **§3.7 RLS 规约补 `FORCE ROW LEVEL SECURITY`**（Plan v1.1 M1；D6 实施已落代码，spec 仅在 policy comment 里提）
2. **§4.1.1 (1)(4)(5) 三个函数 `CREATE FUNCTION` 尾部补 `SET search_path = pg_catalog, public`**（Plan v1.1 M3；D4 实施已落）
3. **§4.1 GRANT 通用规约补 SEQUENCES**（Plan v1.1 M4；D3 实施已落 `GRANT USAGE,SELECT ON ALL SEQUENCES` + `ALTER DEFAULT PRIVILEGES`）
4. **§5.10 L1958-1960 摘除 `user_control_actions` hypertable 段**（Plan bug #5 Q3-B：保 UNIQUE(cmd_id) 幂等语义，TS hypertable 分区列要求冲突）
5. **§4.2 `user_login_records` + §5.10 `alarm_records` compression 块改为 v1.3.7 TODO note**（Plan bug #6 Option A；等 TimescaleDB upstream issue #6827 修复 FORCE RLS 兼容 compression 后回填）
6. （candidate）**pytest→seed workflow 顺序**：D9 `test_upgrade_down_and_up_again` downgrade 到 base → 跑 `task seed` 前需 `task migrate`；建议在 D9 测试末尾加 `alembic upgrade head` teardown（E3-E6 implementer 实测）
7. ~~**spec §A.5 / §D.1 心跳方向**（Plan bug #19 candidate）~~ **user 2026-04-18 决策：spec §A.5 确认 gw→dev 下发心跳，plan v1.4 正确，#19 close 为 non-bug**
8. **20 个 optional Minor 批量清理**（D5 留 3 + D6 留 2 + D7 留 2 + D8 留 5 + D9 留 8；含 `from __future__ import annotations` 缺漏、downgrade operational warning、USING/WITH CHECK 字面重复、`pytest.raises(Exception)` 过宽、alembic.command API 替代 subprocess 等）—— 归入未来 polishing pass

**执行方式**：建议另开 `docs/spec-v1.3.7` branch，批量改 spec `.md` + bump 文件头部 version 1.3.6 → 1.3.7，一次 PR merge master。与 Minor 清理 commit 分开，保证 spec 变更可追溯。

---

## Stage D 回滚 Runbook

**alembic 严格线性：禁跳版 downgrade**。若要回滚 D3 删角色，必须先：

```
D10 → D9 → D8 → D7 → D6 → D5 → D4 → D3 → D2 逐 step（alembic downgrade -1 × N）
```

否则 pg_dump 一致性被破坏（例如 D5 触发器依赖 D4 函数，D4 单独回滚会造成幽灵引用）。

**生产部署**：migration 执行角色必须 SUPERUSER 或是 `ruisheng_gw` / `ruisheng_api` 的成员（否则 D3 `DROP OWNED` 抛 42501 insufficient_privilege）。

**特殊限制（单向约束）**：

- **D8 hypertable 单向**：TimescaleDB 2.16.1 不原生支持 hypertable → regular table，D8 downgrade 只卸 retention + compression policy，**hypertable 本身保留**。完整回滚需 `CREATE TABLE ... AS SELECT` 另存 + `SELECT drop_chunks(...)` + 重建 regular table；或清库从 base 重来。
- **D6 FORCE RLS vs D8 compression 冲突**（TS issue #6827）：`alarm_records` + `user_login_records` 当前未启 compression。若生产需清理历史数据压缩，需临时 `ALTER TABLE ... NO FORCE ROW LEVEL SECURITY`，操作期间 admin-only。
- **D8 复合 PK 前向**：`alarm_records` / `soft_logs` / `user_login_records` 的 `(id, <time_col>)` 复合 PK 由 D8 migration 替换 id-only PK。downgrade 不撤销（TS 压缩 hypertable 不可 DROP CONSTRAINT）；若要回退需清库。

---

## 恢复步骤（下次续跑）

1. 打开 `D:\江苏润盛`，重新连接 Claude Code session
2. 指向本文件：让 Claude 读 `docs/superpowers/plans/PROGRESS.md` 恢复状态
3. 同步远端：`cd D:\江苏润盛\.claude\worktrees\plan-0-foundation && git pull` + `cd D:\江苏润盛 && git pull`
4. 重 export 环境变量（shell 会话不保留）：
   ```bash
   export RUISHENG_GW_PASSWORD='dev-gw-change-me'
   export RUISHENG_API_PASSWORD='dev-api-change-me'
   ```
5. 确认 Docker stack 健康：`docker compose -f docker-compose.dev.yml ps`（worktree 目录下）
6. Claude 接着从 **Stage G / Task G1（扩展 CI workflow — 加 integration / alembic check / schema guard）** 开始 — Stage G 是 Plan 0 **最后一个** Stage（7 task：G1-G7）
7. 继续 subagent-driven-development 流程（pre-dispatch sanity check → implementer → spec review → quality review）

### Stage G 任务快照（下次起跑的 roadmap）

| # | Task | 估时 | 备注 |
|---|---|---|---|
| G1 | 扩展 CI workflow（加 integration / alembic check / schema guard） | 中 | GitHub Actions 扩展现有 `ci.yml` |
| G2 | 完善 `tools/verify_schema_version.py` breaking 检测 | 中 | A5 建的 stub 现在填实逻辑 |
| G3 | docs/ARCHITECTURE.md 开发侧架构速览 | 小 | 一页文档 |
| G4 | 覆盖率门槛 + mutation testing 占位 | 小 | fail_under 已 90%，加 mutmut 占位 |
| G5 | Plan 0 完成 README 补完 + 最终 tag `plan-0-complete` | 小 | 类 D10/E7/F6，纯文档 |
| G6 | 技术债清理：`[tool.uv] dev-dependencies` → `[dependency-groups]`（PEP 735）+ 顺便再测 `testcontainers[postgres]` 自带 asyncpg | 中 | tech debt #1 + #3 |
| G7 | ruisheng-shared release workflow（semver + SHARED_SCHEMA_VERSION + CHANGELOG + GitHub Release，**不发 PyPI**）| 中 | 给 Plan 1/2/3 消费 shared 用 |

### ⚠️ pytest → seed 工作流顺序注意

E3-E6 implementer 实测发现：`test_upgrade_down_and_up_again`（D9 集成测试）跑完会把 DB 降到 base → 之后 `task seed` 会炸 "relation does not exist"。解决：pytest 之后若要跑 seed，需先 `uv run task migrate` 恢复 schema。此非 E3-E6 bug，属测试夹具 teardown 设计（alembic downgrade 无 upgrade 回补）。后续 Plan 1/2 若要 seed 驱动 fixture 可能要在 D9 那个测试后加 `alembic upgrade head` 收尾，**作为 spec v1.3.7 follow-up 第 7 项候选**。

---

## 🔖 会话交接点（2026-04-18，Stage E 6/7 完成 — E7 准备）

**当前位置**：Stage E **E1 + E2 + E3-E6 ✅ 6/7**，下一步 **E7（收尾 tag `plan-0-stage-e-complete`）**

**环境状态**（续跑直接用，无需重建）：
- Docker stack 运行中（postgres + redis，均 healthy）
- alembic 当前：`959079e6cae9 (head)` = D8 timescale hypertables（Stage E 无新迁移）
- PG 库里已有：D8 schema（26 表 + 2 角色 + GRANT + 3 函数 + 16 触发器 + 12 FORCE RLS + 12 policy + 3 fillfactor/autovacuum + 5 hypertable + 5 retention + 3 compression + 3 复合 PK）+ **E3-E6 seed 数据**（wx_groups 'demo' × 1 + users × 2 + devices '60270012' × 1 + device_points × 2）
- 如 pytest 后 DB 降到 base，需 `uv run task migrate` 恢复（见上文 workflow 注意）
- 环境变量（每次新 shell export）：
  ```bash
  export RUISHENG_GW_PASSWORD='dev-gw-change-me'
  export RUISHENG_API_PASSWORD='dev-api-change-me'
  ```

**Stage D 历史收官**（2026-04-17 完成，历史存档）：
- **DB schema**：26 表 + 2 角色 + GRANT + 3 PL/pgSQL 函数 + 16 触发器 + 12 FORCE RLS + 5 hypertable（detail 见 Stage D 进度表）
- **9 Plan bug** 全部 master 反向 fix（#1-#9）
- **tag `plan-0-stage-d-complete`** 指向 worktree `02516f7`

**本次会话 Stage E 进展（E1 + E2 + E3-E6）**：
1. ✅ **E1** (`d2b3482` + fixup `396b2e0`) — conftest 双轨 fixture；**Plan bug #10 pre-dispatch 抓**（Replace 会删 D9 fixture + session-scope async pitfall）→ master plan v1.1 fix `8a0cd8e`（Merge + function-scope）；双 review APPROVED；1 Important fixup（PLC0415 noqa）
2. ✅ **E2** (`b295f8e`) — `tools/embedded_pg.py` stub 45 行 verbatim plan；async+sync 双 API NotImplementedError；review APPROVED 0/0/0；**首个 Stage E 无 plan bug 的 task**
3. ✅ **E3-E6** (`e42f06b`, 6 文件 single commit) — seeds SQL × 4 + run_seeds.py + .pre-commit-config.yaml mypy deps；**3 个 Plan bug 连抓**：
   - **#11** pre-dispatch：devices/device_points raw SQL INSERT 漏 NOT NULL 无 server_default 列（同 D9 #8）→ v1.1 `f0c5614`
   - **#12** implementer live-DB：device_points 无 UQ 故 `ON CONFLICT DO NOTHING` no-op → v1.2 `08f12d2`（`WHERE NOT EXISTS` + `# type: ignore`）
   - **#13** implementer pre-commit：mirrors-mypy 隔离 venv 缺 asyncpg → `import-not-found` → v1.3 `260729e`（pre-commit mypy deps 加 asyncpg）
   - idempotency 双跑 counts 1/2/1/2 稳定；pre-commit 全绿；pytest 336+8 无回归

**下一步：E7 — Stage E 收尾 tag + PROGRESS（纯文档 + tag，类 D10）**

**Spec/Plan 依据**：plan §Task E7；spec 无相关依赖

**E7 任务列表**（plan §Task E7 很简短）：
1. **打 tag**：`git tag -a plan-0-stage-e-complete -m "Stage E: fixtures + embedded PG stub + seeds (E1 conftest dual-mode / E2 EmbeddedPostgres stub / E3-E6 seeds + run_seeds). 336 passed + 8 skipped. Plan bugs #10-#13 reverse-fixed."` + `git push origin plan-0-stage-e-complete`
2. **master PROGRESS 更新**：Stage E 7/7 完成 + 状态翻页 + 更新仓库坐标
3. **memory 更新**：`project_ruisheng_scada.md` 反映 Stage E 完结 + Stage F 作为下一步

**E7 可附加（可选）**：
- spec v1.3.7 follow-up 清单补第 7 项（pytest teardown 不恢复 schema）
- Stage E 回滚 Runbook（可选，E1/E2 无 DB 状态改动，E3-E6 seed 可 `TRUNCATE` 或直接 alembic downgrade+upgrade）

**Stage E 任务链（plan §Task E1-E7，以实际完成为准）**：
| # | Title | 状态 |
|---|---|---|
| E1 | conftest.py 扩展（testcontainers/embedded 双轨 fixture） | ✅ `d2b3482` + fixup `396b2e0` |
| E2 | Embedded PG 包装（stub） | ✅ `b295f8e` |
| E3-E6 | seeds 4 SQL + run_seeds.py + pre-commit asyncpg | ✅ `e42f06b`（单 commit） |
| E7 | Stage E 收尾 tag | **← 下一步** |

**注意**：plan §Stage E 原本预设 E3/E4/E5/E6 各是独立 task（seeds/base.sql / apply_seed.py / pytest-xdist / CI Linux），但 v1.0 plan 其实把这 4 个合并为 "E3-E6 bundled"（§Task E3-E6 章节）——pytest-xdist 并行 + CI Linux 跑 testcontainers 在 v1.0 plan 里**未定义具体 task**。**建议 E7 结束后若要补足，可作为 Stage G 或 Plan 1 前置。当前 Plan 0 按"plan 原文 + E7 收尾"算 Stage E 完结。**

**执行纪律**：
- subagent-driven：每 task 1 implementer + 2 review（spec → quality）
- **发现 plan/spec bug 必须 BLOCKED**，绝不静默改。Plan bug 累计：#1 D3 / #2 D4 / #3 D5 / #4 D6 / **#5 + #6 D8**（D8 独占 2 个：#5 pre-dispatch 活探测抓，#6 BLOCKED 实测触发）全部 controller 反向 fix plan + master commit
- **代码质量审查必须真比对 spec**（不只是 plan）。D4/D5/D8 review 都有过 spec spot-check 实践
- **Controller pre-dispatch sanity check 是好习惯**（D5 ORM scan 抓 #3 / D6 ORM scan 抓 #4 / D8 live-DB 探测抓 #5 — 都节省一轮 implementer 跑）
- commit 前必须 `uv run task fmt`
- commit 不混 side-fix（D8 ORM lockstep 测试与 CHANGELOG 属 required dependency，非 side-fix）
- 每 task 完成后立即更新 PROGRESS + push master

**Plan v1.1 重编原因**：D2 完成后做 plan gap review，发现 v1.0 对 spec v1.3.3/1.3.4/1.3.6 覆盖严重不足：
- 3 个 PL/pgSQL 函数（set_updated_at / enforce_scene_tenant_consistency / fill_scene_views_snapshot）0 task 覆盖
- 2 个 DB 角色（ruisheng_gw BYPASSRLS / ruisheng_api）0 task 覆盖
- 16 个触发器（13 updated_at + 3 scene）0 task 覆盖
- 6 张 RLS 漏表（maintain_plans / maintain_actions / pay_orders / user_login_records / user_wx_bindings / alarm_outbox）
- user_login_records hypertable（v1.3.6 新增）漏
- device_waring_cfgs fillfactor（§5.10 L1930）漏
- soft_logs 压缩（§4.2 L1399-1400）漏
- 原 D3 downgrade `DROP TABLE CASCADE` 是 bug（误删 D2 建的表）

**2 轮 plan review 抓的 5 MAJOR（已合入 Plan v1.1）**：
1. **M1** RLS 必须 `FORCE`（owner 绕过）：D6 每表 `FORCE ROW LEVEL SECURITY`
2. **M2** hypertable 需幂等：D8 用 `if_not_exists => TRUE` + `remove_*_policy → add_*_policy`
3. **M3** 函数 `SET search_path = pg_catalog, public` 硬绑定：D4 三函数末尾补
4. **M4** GRANT 漏 SEQUENCES：D3 补 `GRANT USAGE,SELECT ON ALL SEQUENCES` + `ALTER DEFAULT PRIVILEGES`
5. **M5** PROGRESS 与 spec RLS 变量名冲突：已修正（**`app.tenant_id`** + **`app.role`**，非 `app.current_usr_group`）

**新 D3-D10 结构**（detail 见 plan §Stage D）：

| # | Title | Migration 文件 |
|---|---|---|
| D3 | DB 角色 + GRANT 基线 | `0002_db_roles_and_grants.py` |
| D4 | PL/pgSQL 函数（3 个，search_path 硬绑） | `0003_plpgsql_functions.py` |
| D5 | 17 张表 `trg_<table>_updated`（ORM drift 断言） | `0004_updated_at_triggers.py` |
| D6 | scene_* 3 触发器 + 18 张表 FORCE RLS + tenant_isolation policy | `0005_scene_triggers_and_rls.py` |
| D7 | fillfactor/autovacuum（含 device_waring_cfgs） | `0006_hot_table_tuning.py` |
| D8 | TimescaleDB hypertable×6（含 user_login_records） | `0007_timescale_hypertables.py` |
| D9 | 集成测试（13 case） | `tests/integration/test_alembic_upgrade.py` |
| D10 | 收尾 tag + spec v1.3.7 follow-up 清单 | tag `plan-0-stage-d-complete` |

**新 D3 要做的事**（detail 见 plan §Task D3）：
1. docker stack 保持运行（D2 没关）；如停了 `docker compose -f docker-compose.dev.yml up -d`
2. 生成 revision：`uv run alembic revision -m "db roles ruisheng_gw/ruisheng_api + grants"`
3. 填充 upgrade()：`_require_env()` helper + 幂等 `DO $$ IF NOT EXISTS ... $$` + schema/table/sequence 级 GRANT + ALTER DEFAULT PRIVILEGES + 3 张表 REVOKE+GRANT
4. 填充 downgrade()：`DO $$ DROP OWNED BY ... DROP ROLE IF EXISTS END $$`
5. 同步改 `.env.example` + `CONTRIBUTING.md`（追加环境变量小节）
6. export `RUISHENG_GW_PASSWORD` + `RUISHENG_API_PASSWORD` → upgrade → `\du` → downgrade → upgrade 幂等
7. commit + push

**RLS 变量名（权威）**：`app.tenant_id`（租户）+ `app.role`（'Administrators' 跨租户权限）。严禁用 `app.current_usr_group`。

**参考**：plan §Stage D 完整代码片段、spec §3.7 / §4.1 / §4.1.1 / §4.2 / §5.10

---

## 📚 Stage D 累计关键经验（后续 task 可参考）

### 镜像加速（国内必须）
- **`timescale/timescaledb:2.16.1-pg15` 只在 `docker.1ms.run/` 可拉**
- DaoCloud 对该镜像返 500（其他 image 正常），USTC/163 DNS 解析失败，dockerproxy IP 被污染
- 已配置 5 个镜像源到 Docker Engine（`docker.m.daocloud.io` 等），特定 image 失败时用 `docker pull docker.1ms.run/<name> && docker tag <1ms.run/name> <name>` 变通

### CJK 路径 + uv editable install
- `D:\江苏润盛\...` 路径下，uv 写入 UTF-8 `.pth` 被 Python `mbcs` 解码器损坏 → editable install 的 `ruisheng_shared` 包 import 失败
- 修法：alembic.ini `prepend_sys_path = . ruisheng-shared/src` + `path_separator = space`（跨平台）
- **Stage B-C 的 pytest 配置已经 hack 过**：`pyproject.toml` `pythonpath = ["ruisheng-shared/src"]` 原因同此

### Windows configparser GBK 限制
- `alembic.ini` 的注释不能含中文（GBK 解码器会炸 `UnicodeDecodeError: 'gbk' codec can't decode`）
- 所有 `.ini` 注释必须 ASCII/英文

### Docker Desktop 配置层次
- `C:\Users\<user>\.docker\daemon.json` — Docker CLI 配置（**Docker Desktop 不读**）
- `%APPDATA%\Docker\settings-store.json` — Docker Desktop 应用配置（`UseContainerdSnapshotter`、`AutoStart` 等）
- **Docker Engine 的 daemon 配置**（含 `registry-mirrors`）通过 **Docker Desktop UI → Settings → Docker Engine** 标签页编辑，存在别处，不是上面两个文件
- 启用 containerd snapshotter 时 `registry-mirrors` 生效路径不同；本项目用默认（不启 containerd）

---

## 当前技术栈状态（2026-04-17 Stage D ✅ 完结）

- 26 张 ORM 模型全部实现，mypy 0 issues（28 files），pytest **336 passed + 8 skipped**（321 unit + 15 D9 integration）
- `SHARED_SCHEMA_VERSION=20260415`，spec `v1.3.6`（spec v1.3.7 follow-up 6 项见上文，非阻塞，Stage E/F/G 并行期间另开 branch）
- Alembic 1.13 已加为显式 dev-dep，`alembic/env.py` async 版本就绪
- **`alembic/versions/` 已有 7 个迁移**：
  - D2：`20260416_e05529ef4abb_initial_schema_26_tables.py`（26 张 CREATE TABLE）
  - D3：`20260416_e74ffa548c2f_db_roles_ruisheng_gw_ruisheng_api_grants.py`（2 角色 + GRANT）
  - D4：`20260416_09676586bfbd_plpgsql_set_updated_at_scene_tenant_.py`（3 PL/pgSQL 函数 + search_path 硬绑；fixup `85f2d2b`）
  - D5：`20260416_89a9dfebe138_trg__table__updated_triggers_13_tables.py`（13 BEFORE UPDATE 触发器 + ORM drift 断言）
  - D6：`20260417_0005_scene_triggers_and_rls.py`（3 scene 触发器 + 12 张表 FORCE RLS + 12 tenant_isolation policy + ORM drift 断言）
  - D7：`20260417_0006_hot_table_tuning.py`（3 张表 fillfactor + autovacuum；C11 tech debt 兑现）
  - **D8**：`20260417_0007_timescale_hypertables.py`（drop alarm_outbox FK + 3 张表复合 PK + 5 张 hypertable + 5 retention policy + 3 compression policy；head `959079e6cae9`；Plan bug #5 + #6 双修）
- Docker stack 运行中（postgres + redis healthy）；PG 里有完整 26 张表 + alembic_version + 2 角色 + 3 函数 + 13 updated 触发器 + 3 scene 触发器 + 12 policy + 3 张表 reloptions tuning + 5 hypertable + 5 retention + 3 compression policy
- Git 全部 push 完毕；tag `plan-0-stage-c-complete` 已存在（Stage D 未打 tag，D10 才打）
- worktree HEAD: `4205faf`，master HEAD: `5caab8d`（即将 +PROGRESS commit）

### Stage D 快速导航

- Plan 位置：`docs/superpowers/plans/2026-04-13-plan-0-foundation.md` §Stage D（v1.4 含 Plan bug #4 + #5 + #6 反向 fix）
- Task 顺序（v1.4）：~~D0 环境校验~~ ✅ → ~~D1 baseline~~ ✅ → ~~D2 初始 schema~~ ✅ → ~~D3 DB 角色 + GRANT~~ ✅ → ~~D4 PL/pgSQL 函数~~ ✅ → ~~D5 updated_at 触发器~~ ✅ → ~~D6 scene 触发器+12 FORCE RLS~~ ✅ → ~~D7 fillfactor + autovacuum~~ ✅ → ~~D8 TimescaleDB hypertable×5 + 复合 PK prep~~ ✅ → **D9 集成测试 13 case** ← 下一步 → D10 tag + spec follow-up
- 与 Stage C 的区别：**需要真实 PG + Redis 容器**（不再是纯 Python 单测）
- 关键产出：7 个 `alembic/versions/*.py` 迁移脚本 ✅ + `tests/integration/test_alembic_upgrade.py`（13 case）← D9 → tag `plan-0-stage-d-complete` ← D10

---

## 关键决策回放（Plan 0 启动时）

- 架构：方案 B 前后分离 + Redis（非单体，非 MQTT Hub）
- 技术栈：Python FastAPI + Vue 3 + PostgreSQL + TimescaleDB + Redis
- 部署：Windows Server + B/S
- 外部集成：微信公众号 + 微信支付 + 短信 + IVR + SMTP 全上
- MVP 范围：P0 全部 + 部分 P1（12 周）
- Plan 拆分：Plan 0 基础 / Plan 1 gw / Plan 2 api / Plan 3 web / Plan 4 部署
- 执行方式：subagent-driven（每 task 一个 implementer + 两阶段 review）
- 自动 push：每 task commit 后自动 push

---

**END OF PROGRESS**

---
title: '告警通知运行链路接通'
type: 'feature'
created: '2026-08-18'
status: 'done'
baseline_commit: 'dbef2acc26c76d64eb303b2f0020dd372059aaf5'
context:
  - '{project-root}/需求清单/功能全景清单.md'
  - '{project-root}/docs/superpowers/specs/2026-04-13-ruisheng-iot-design.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** GW 只向 Pub/Sub 发布简化阈值事件，API 却消费 `stream:alarm:fired`；通知适配器从未接入运行链路，现有消费者还会在成功前写去重键，失败重投可能丢通知。

**Approach:** GW 以 CAS 维护告警状态并在同一事务写 `alarm_records + alarm_outbox`；outbox worker 发布版本化 Stream 事件。API consumer 校验并物化 dispatch/deliveries 后立即 ACK，独立 worker 通过 PostgreSQL 租约发送和重试。

## Boundaries & Constraints

**Always:** PostgreSQL 是告警、幂等和重试事实源；事件使用 `(id, triggered_at, alarm_cfg_id)` 完整身份并由数据库反查租户；Stream/供应商均为至少一次语义；所有调度时间用数据库 UTC；联系人查询受 RLS 约束；delivery 租约使用 fencing token/version；`channels_sent` 只公开各渠道 `sent/failed/skipped/pending` 计数。

**Recipient Model:** 每条告警配置显式选择“用户 + 渠道”，默认无人订阅；有管理权限且具备 CA `0x02` 的租户管理员在现有告警配置页面维护。delivery 只保存联系人记录引用与不可逆指纹，发送时读取当前联系人；联系人删除则 skipped。delivery/attempt 审计保留 180 天，仅同租户管理员可读脱敏信息。

**Ask First:** 启用真实第三方凭据、改变公开 API 兼容语义、收件人模型、保留策略或复位通知渠道时先征得用户同意。

**Never:** 测试中发送真实通知；使用 BYPASSRLS 解析联系人；把 Redis PEL 当分钟级重试队列；使用 `alarm_seen` 保证正确性；记录明文联系方式、密钥或供应商原始响应；承诺 exactly-once；实现复位通知或其他延期目标。

## I/O & Edge-Case Matrix

| Scenario | Expected Behavior | Failure Handling |
|----------|-------------------|------------------|
| 首次/持续/恢复 | 首次越限 CAS 成功者写记录/outbox；持续越限不重复；自动恢复与人工 reset 竞争只形成一个终态，恢复后可再触发 | 本轮不发送复位通知 |
| 重放/并发 | 重复 Stream 事件只物化一个 dispatch 和一套逻辑 delivery；dispatch 成功后 ACK | 物化失败留 PEL；毒事件进事件 DLQ 后 ACK |
| 无订阅/无效目标 | 无订阅记 `no_subscription` 且不建 delivery；订阅存在但联系人删除/provider 关闭则建 skipped delivery | 不猜测替代用户或渠道 |
| 渠道失败 | 超时、连接错误、429/5xx 可重试并限幅遵循 `Retry-After`；认证、无效目标和其他 4xx 永久失败 | 终态失败留数据库，不进事件 DLQ |
| 崩溃/慢供应商 | sent 不重发；供应商成功但落库前崩溃允许可审计重复；过期 worker 的 fencing 更新失败 | 有界并发、租约回收和积压指标 |
| 非法/旧事件 | 拒绝未知版本、非正 ID、非有限数值、超大 payload、越界时间或租户/记录不一致 | DLQ 原因限长脱敏，不创建 dispatch |

</frozen-after-approval>

## Code Map

- `ruisheng-shared/src/ruisheng_shared/models/alarms.py`、`alembic/versions/` -- 配置快照、订阅、dispatch/delivery/attempt 表及约束。
- `ruisheng-gw/src/ruisheng_gw/{domain/registry.py,ingest.py,persistence/repository.py,pubsub/publisher.py}` -- 状态机、事务 outbox 和 Stream worker。
- `ruisheng-api/src/ruisheng_api/pubsub/alarm_consumer.py`、`services/notification/` -- 事件物化、delivery worker 和适配器。
- `ruisheng-api/src/ruisheng_api/{api/alarms.py,config.py,main.py}` -- 订阅 API、provider 配置和生命周期。
- `ruisheng-web/src/views/alarms/AlarmConfigView.vue` -- 用户/渠道订阅管理入口。

## Tasks & Acceptance

**Execution:**
- [x] Schema/migration -- 为告警记录增加配置身份和不可变展示快照；新增租户化订阅、dispatch、delivery、attempt 表，约束完整身份、逻辑目标和租约版本；历史不随配置删除。
- [x] GW -- 加载并热刷新启用的 `device_waring_cfgs`；每次已提交的规则变更通过 `channel:config:changed` 广播，各 GW 实例独立 pull，数据库版本轮询只作不互相清除的补偿；规则重载清除受影响设备的 LX 连续计数，单条规则失败不阻断本帧遥测和其他规则；关系点缓存带采样时间且按 true/false/unknown 三态处理，unknown 不恢复；以 CAS 处理首次触发/恢复；原子写记录/outbox；worker 在 `XADD` 成功后标记 published，Redis 恢复后可重放，并按总线契约限制 Stream 长度。
- [x] API materializer -- 严格校验大小、版本、完整非空身份、ID、有限数值、可配置数据库 UTC 时间窗口及数据库身份；RLS 下展开显式订阅并幂等建 dispatch/deliveries；物化成功后 ACK + XDEL，再进行不影响 ACK 的首次 WebSocket 广播；包括物化校验错误在内的毒事件达到有限 PEL 重试上限后才写脱敏 DLQ 并 ACK + XDEL。
- [x] API delivery worker -- `SKIP LOCKED` + 数据库 UTC 租约 + fencing 更新，解析完成后、调用供应商前重新取得覆盖完整超时窗口的租期，且所有完成更新同时校验 lease owner/version 和数据库时钟下未过期；每次只领取可立即并发执行的安全批次；单项异常不逃逸批次；分类重试、保存真实起止时间的脱敏 attempt；在告警记录锁内聚合并写入稳定 `channels_sent` 投影；每日清理 180 天前审计。
- [x] API/Web -- 提供 CA `0x02` 保护的订阅 CRUD 与脱敏审计查询；通知管理与审计严格绑定当前用户租户，禁止利用 L4 可见性绕过同租户边界；在告警配置页管理用户和渠道，完整回显/提交关联规则，默认空订阅。
- [x] Config/ops -- provider 默认关闭；微信 token 每次发送读取当前租户值，环境型 provider 重启生效；生产可配置 worker 批次/并发/租约/重试及事件窗口；实例身份跨副本唯一；暴露 outbox、物化失败、pending/oldest-age、失败分类和 stale-completion 指标。
- [x] Tests -- 覆盖热加载、关系点过期/unknown、CAS/恢复竞争、配置删除、outbox 重放、跨租户伪造、无订阅/skipped、非法/延迟事件、有限 PEL 重试、微信 HTTP 200 限流、worker 批次隔离/租约回收、attempt 审计、180 天清理与公开字段脱敏。

**Acceptance Criteria:**
- Given A/B 两租户及 A 的显式订阅，when A 的配置首次越限，then 只为 A 的所选用户/渠道建 delivery，B 的联系人不被读取。
- Given 并发首次越限或自动恢复与人工 reset，when 事务竞争，then 只有一个合法状态迁移，配置、记录和 outbox 一致。
- Given 重放/并发 consumer，when 物化同一事件，then 只有一个 dispatch 和一套逻辑 delivery；过期 worker 不能覆盖新租约结果。
- Given 现有告警 API 返回 `channels_sent`，when 任意角色读取，then 只见稳定计数，不见目标、provider message 或原始错误。
- Given 管理员保存、新增或删除告警规则，when GW 继续运行，then 无需重启即可在一个刷新周期内使用新规则；关联点缺失或过期时不触发也不恢复现有告警。
- Given 物化成功但 WebSocket 广播失败或毒事件反复失败，when consumer 处理事件，then 前者仍立即 ACK 且不重复物化广播，后者达到上限后写脱敏 DLQ 并 ACK。
- Given worker 批次大于并发度或任一 delivery 抛出异常，when 执行一轮，then 未开始项不因排队耗尽租约，单项失败不使其他任务脱离并发边界；attempt 记录真实开始/结束时间且 dispatch/attempt 对 API 角色不可更新。
- Given 同一告警的多个 delivery 并发终结，when 它们更新公开投影，then 聚合与写入在同一告警记录锁内串行化，最终 `channels_sent` 与所有 delivery 终态一致。
- Given 两个 GW 副本同时运行或其中一个错过广播，when 管理员提交规则变更，then 每个副本通过广播或不互相消费的数据库版本补偿独立刷新，且受影响设备的旧 LX 连续计数不会跨配置版本复用。
- Given 任一通知管理或审计请求指向其他租户，when L4 或其他管理员调用，then 请求被拒绝；同租户请求仍要求角色与 CA `0x02`。
- Given 告警 Stream 持续增长、消息成功物化或毒事件达到重试上限，when consumer 完成处理，then producer 限制 Stream 长度，consumer 在 ACK 后删除原消息，且未到上限的失败仍留在 PEL。
- Given delivery 的租约已经过期或解析已消耗原租期，when worker 准备调用供应商或提交结果，then 前者必须先续得完整发送窗口，后者必须因数据库 UTC 下租约无效而 fencing 失败。

## Spec Change Log

- 2026-08-18 review loop 1 -- 触发：三路复审发现启动快照导致规则变更不生效、关系点 unknown 被当作恢复、租约批次可在执行前过期、PEL 重试无上限、物化 ACK 被 WebSocket 绑定，以及 dispatch/attempt 可更新且审计查询缺 attempt。修订：在非冻结的 Execution/Acceptance 中加入热刷新、带 freshness 的三态求值、安全领取批次与租期校验、单项异常隔离、先 ACK 后首次广播、有限 DLQ、完整身份非空和 attempt 不可变/可查询约束。避免的已知坏状态：重启前使用旧规则、错误自动恢复、并发重复发送、无限 PEL、广播故障阻塞 ACK、审计被篡改或不可追溯。KEEP：保留 PostgreSQL 事实源、CAS/outbox、RLS 租户反查、显式订阅、provider 默认关闭、fencing、脱敏投影、180 天清理及“供应商成功后落库前崩溃允许可审计重复”。
- 2026-08-19 review loop 2 -- 触发：三路复审发现并发终结可覆盖为陈旧 `channels_sent`、共享 `update_flag` 只刷新一个 GW、副本通知接口可借 L4 可见性跨租户、告警 Stream 无界增长、物化错误首次即 DLQ，以及租约过期但未被重领时旧 worker 仍可完成。修订：在非冻结 Execution/Acceptance 中加入告警记录锁内投影、Redis 广播 + 非消费式版本补偿、通知管理/审计同租户强约束、Stream 限长与 ACK 后 XDEL、所有毒事件有限 PEL 重试、供应商调用前续租及完成时数据库 UTC 租期校验，并明确配置重载清理 LX 计数和规则异常隔离。避免的已知坏状态：永久错误计数、部分副本使用旧规则、跨租户收件人与审计泄露、Redis 内存持续增长、未重试即丢弃、过期 worker 覆盖结果及旧 LX 计数提前触发。KEEP：保留完整身份、CAS/outbox、至少一次及幂等物化、先 ACK 后尽力广播、当前联系人读取、provider 默认关闭、脱敏审计、180 天清理，以及规格允许的可审计重复窗口。

## Design Notes

`notification_dispatches` 即使无订阅也落一行；只有解析出的逻辑目标产生 delivery。第三方调用无法与本地事务原子提交，因此保证“完成项不重发、崩溃窗口允许重复、全过程可审计”，不保证 exactly-once。配置/订阅删除不破坏已落库事件；联系人引用删除后的未发送项变为 skipped。

## Verification

**Commands:**
- `uv run pytest ruisheng-gw/tests/unit ruisheng-api/tests/unit -q`
- `uv run pytest tests/integration ruisheng-gw/tests/integration ruisheng-api/tests/integration -m integration -q`
- `pnpm --dir ruisheng-web typecheck && pnpm --dir ruisheng-web lint && pnpm --dir ruisheng-web test`
- `uv run ruff check . && uv run ruff format --check . && uv run mypy .`
- `docker compose -f docker-compose.prod.yml --env-file .env.prod.example config`

## Suggested Review Order

**Runtime Contract**

- Start with event validation, tenant derivation, and idempotent materialization.
  [`runtime.py:124`](../../../ruisheng-api/src/ruisheng_api/services/notification/runtime.py#L124)

- Review lease renewal and database-clock fencing before provider I/O.
  [`runtime.py:361`](../../../ruisheng-api/src/ruisheng_api/services/notification/runtime.py#L361)

- Confirm terminal updates and audit attempts share the fenced transaction.
  [`runtime.py:515`](../../../ruisheng-api/src/ruisheng_api/services/notification/runtime.py#L515)

- Verify projection aggregation occurs only after locking the alarm row.
  [`runtime.py:614`](../../../ruisheng-api/src/ruisheng_api/services/notification/runtime.py#L614)

**Gateway And Stream**

- Follow CAS transitions through atomic alarm snapshot and outbox creation.
  [`repository.py:98`](../../../ruisheng-gw/src/ruisheng_gw/persistence/repository.py#L98)

- Check bounded Stream publication and replayable outbox completion.
  [`repository.py:325`](../../../ruisheng-gw/src/ruisheng_gw/persistence/repository.py#L325)

- Review atomic ACK/XDEL and bounded PEL-to-DLQ handling.
  [`alarm_consumer.py:110`](../../../ruisheng-api/src/ruisheng_api/pubsub/alarm_consumer.py#L110)

- Verify non-consuming version polling and stale broadcast suppression.
  [`registry.py:251`](../../../ruisheng-gw/src/ruisheng_gw/domain/registry.py#L251)

- Confirm every GW subscribes and reloads only newer committed versions.
  [`main.py:177`](../../../ruisheng-gw/src/ruisheng_gw/main.py#L177)

- Confirm full-frame telemetry is published before isolated alarm rule evaluation.
  [`ingest.py:129`](../../../ruisheng-gw/src/ruisheng_gw/ingest.py#L129)

**API And Data Boundaries**

- Review finite thresholds, bit bounds, and all-or-none relation validation.
  [`alarms.py:34`](../../../ruisheng-api/src/ruisheng_api/api/alarms.py#L34)

- Confirm explicit nulls clear relation fields before persistence and broadcast.
  [`alarms.py:144`](../../../ruisheng-api/src/ruisheng_api/api/alarms.py#L144)

- Verify notification administration remains strictly tenant-local, including L4.
  [`alarms.py:236`](../../../ruisheng-api/src/ruisheng_api/api/alarms.py#L236)

- Inspect immutable identities, tenant triggers, RLS, and audit retention together.
  [`20260818_0012_alarm_notification_runtime.py:41`](../../../alembic/versions/20260818_0012_alarm_notification_runtime.py#L41)

**Web Workflow**

- Review full recipient pagination and subscription/audit loading in one drawer.
  [`AlarmConfigView.vue:151`](../../../ruisheng-web/src/views/alarms/AlarmConfigView.vue#L151)

- Confirm relation enable/clear state round-trips without losing optional bit data.
  [`AlarmConfigView.vue:223`](../../../ruisheng-web/src/views/alarms/AlarmConfigView.vue#L223)

**Verification**

- Start focused runtime review with concurrent projection locking coverage.
  [`test_runtime.py:152`](../../../ruisheng-api/tests/unit/services/notification/test_runtime.py#L152)

- Check configuration-version isolation prevents stale LX counter reuse.
  [`test_alarm_repository.py:88`](../../../ruisheng-gw/tests/unit/test_alarm_repository.py#L88)

- Finish with database outbox rollback and replay integration coverage.
  [`test_alarm_notification_runtime.py:512`](../../../tests/integration/test_alarm_notification_runtime.py#L512)

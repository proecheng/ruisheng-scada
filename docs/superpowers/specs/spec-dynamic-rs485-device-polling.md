---
title: 'RS485 下位机动态增减与顺序轮询'
type: 'feature'
created: '2026-09-07'
status: 'done'
baseline_commit: 'd839330a91cdf7cae3f4c30aff9396fb99a47120'
context:
  - 'docs/superpowers/specs/spec-dynamic-rs485-device-polling-investigation.md'
---

<frozen-after-approval reason="human-owned intent">

## Intent

**Problem:** 用户需要按设备列表轮询多个下位机。现有串口映射只在启动时建立，总线锁仅覆盖发送，没有等待响应。

**Approach:** 复用设备增改、停启和软删界面；按已配置串口串行调度，设备数量来自列表，不写死为 5 或 9。

## Boundaries & Constraints

**Always:** 同总线仅一个在途请求；地址 1..247 不重复、不必连续，沿用架构每总线最多128台的限制。保留租户权限、历史数据及其他型号默认行为。

**Ask First:** 真机通信、部署、生产迁移、打开新串口及正式采集另行审批。

**Never:** 自动扫描地址、设备寄存器写入、绕过点表验收、修改旧诊断白名单。TCP 热更新、自动发现和模板原子复制不在本轮。

## I/O & Edge-Case Matrix

| 场景 | 输入 | 预期行为 | 异常 |
|---|---|---|---|
| 五台 | 同串口地址1..5 | 逐台FC3从0读38；正常帧81字节 | CRC动态计算 |
| 增减 | 新增、停启、软删、改地址 | 无需重启；空列表零TX | 不重用旧请求上下文 |
| 超时 | 某台无回复 | 默认1秒截止、轮内不重试、继续其他台 | 不写虚假数值 |
| 冲突 | 相同串口及地址 | 非删除记录拒绝重复，包括停用设备 | 并发冲突返回业务错误 |
| 迟到 | 非当前请求的响应 | 不入库、不完成当前事务 | 记录诊断 |

</frozen-after-approval>

## Code Map

路径、现状证据及任务子文件详见配套调研文档的 T1..T6。

## Tasks & Acceptance

**Execution:**

- [x] T1 `ruisheng-shared/src/ruisheng_shared/models/devices.py`：增加读取方案和迁移；修正软删地址索引。
- [x] T2 `ruisheng-api/src/ruisheng_api/api/devices.py`：方案校验、版本自增及提交后通知，统一停用入口；点位变更共用通知。
- [x] T3 `ruisheng-gw/src/ruisheng_gw/domain/registry.py`：全量集合对账，发现从空列表新增；刷新设备、点位及告警。
- [x] T4 `ruisheng-gw/src/ruisheng_gw/scheduler/serial_poller.py`：新增总线事务调度，接入串口收发与生命周期；不启动第二套串口轮询。
- [x] T5 `ruisheng-web/src/views/devices/DeviceListView.vue`：显示从站地址；创建/编辑提供读取方案并保留现有操作。
- [x] T6 `tests/integration/test_serial_device_lifecycle.py`：模拟五台、动态增减、异常及数据库归属；补单测与浏览器验收。

**Acceptance Criteria:**

- Given 五台模拟设备选择zero_origin_38且点位有效，When 运行两个轮次，Then 每轮各读一次，上一事务结束前不发送下一条。
- Given 运行中增减及连续两次修改，When 通知到达或5秒兜底刷新，Then 配置集合更新；下一调度使用新配置，删除生效后不发新请求。
- Given 各设备返回不同原值，When 经采集、写库、API查询，Then 原值、实时值及历史按设备和点位隔离，其他租户不可读。
- Given 一台超时、异常或返回坏帧，When 其事务结束，Then 其余设备继续；无匹配有效响应不产生测量记录。
- Given 删除后用新设备号复用地址，When 新增提交，Then 历史保留、旧事务失效；停用但未删仍占地址。

## Spec Change Log

- 2026-09-07：独立审查后的局部修复补齐旧回调清理身份、TCP锁后重验、串口处理超时与告警代际检查；补通知/周期入口测试。冻结审批意图不变，TCP既有交库顺序保留。

## Design Notes

设备级 `read_profile` 默认 `point_groups`；串口可选 `zero_origin_38`，固定FC3/0/38，独立于显示点位是否稀疏；无合法点位不发包。该方案拒绝非FC3或越界点位，不推定单位、倍率及负数编码。

按到期设备公平轮转，保留设备间隔，不追赶积压；事务含发送、响应或超时及200ms静默。仅匹配串口、从站、功能码、长度、CRC和当前配置代际的响应进入原有采集链；同地址重绑定需旧事务结束并清理残帧。RTU没有事务号，不能声称软件可识别任意延迟的同形旧帧。

接收后的异步发布/告警处理另以响应超时时长为上限（默认1秒），超时取消并记录诊断，随后继续其他设备。合法串口帧的全部测量值在异步发布前同步提交BatchWriter；已接收数据不会因发布取消而漏交，但下游存储故障仍遵循原队列/WAL机制。旧回调只能清理自身pending，不能清除换端口后的新事务。

全集合刷新复用现有5秒周期及Redis通知；提交前不广播，刷新失败保留旧快照并报告错误。只热更新已配置串口的设备；不改变目标机现场配置。

## Verification

实施验证：T1/T2 针对性单测 75 passed；API 单测与共享模型扩大回归 631 passed、8 skipped（原有占位项）；涉及 API 源码 Ruff、Mypy 通过，Alembic 单一 head 为 `0013_serial_polling_profile`。本轮自建 Testcontainers Timescale 测试库完成 `test_alembic_upgrade.py` 全部 15 项，含升级、回退再升级和 RLS。

T6 最终复跑：`tests/integration/test_serial_device_lifecycle.py` 6 passed，76.53秒。使用真实 API/JWT、受 RLS 限制的 API 数据库角色、网关、批量写入器及专用 Timescale 测试容器；串口为内存模拟，Redis 为 FakeRedis。覆盖五台两轮黄金指令与原值/缩放值/历史归属、增至6台再停用至5台及删除至4台、地址复用历史保留、并发冲突及租户隔离、坏帧/超时/旧代际不入库、128台容量、迁移安全回退。新增用例运行生产订阅与周期刷新函数，验证通知自动生效，以及订阅断开后默认5秒周期自动应用改址，无手工调用触发。测试容器已自动清理。

前端最终单测105 passed；登录、首页、设备和全流程浏览器模拟测试26 passed，54.7秒；typecheck、build及相关ESLint通过。1366px和390px的列表、新建、编辑共六张截图已目视核查。浏览器接口模拟不替代上述真实后端测试，也不代表现场验收。

发布制品及验收回执相关本地工具回归413 passed、5 skipped（平台特定项）；实际仓库迁移head断言已跟随新版本。测试未执行上传、部署或远程操作。

收尾修复：告警运行锁存状态不再被误判为配置变更；RealClock复核单调时钟期限，避免Windows计时提前唤醒破坏串口最小静默/超时约束，新增提前唤醒、零/负数等待和取消回归。独立盲审、边界审查和验收审查完成，4项运行时竞态/阻塞问题、自动刷新覆盖缺口及读取方案错误提示均已修复或补测，边界审查员复核通过。最终后端联合回归（API、共享模型、网关单测及性质测试）871 passed、8 skipped，42.09秒；GW全部40个源码文件Mypy、相关Ruff及diff-check通过。原有LX计数清理竞态记入[延期事项](deferred-work.md)，不扩展本轮范围。

未执行：目标机连接、物理串口通信、2..5号实物测试、部署或生产迁移。点位单位、倍率及编码仍需现场核实，不以本轮合成数值替代正式校准。

验收汇总：[本地验收报告](../../reports/2026-09-07-dynamic-rs485-local-acceptance.md)。未创建提交或推送；保留工作区既有改动。

## Suggested Review Order

**串口事务与热更新**

- 从单总线调度入口理解完整事务、超时与静默。
  [serial_poller.py:36](../../../ruisheng-gw/src/ruisheng_gw/scheduler/serial_poller.py#L36)
- 核查配置集合对账与未变设备状态保留。
  [registry.py:237](../../../ruisheng-gw/src/ruisheng_gw/domain/registry.py#L237)
- 核查回调身份清理及自动刷新入口。
  [main.py:38](../../../ruisheng-gw/src/ruisheng_gw/main.py#L38)

**契约与界面**

- 完整状态校验、版本递增与提交后通知。
  [devices.py:99](../../../ruisheng-api/src/ruisheng_api/api/devices.py#L99)
- 地址软删复用及拒绝不安全回退。
  [20260907_0013_serial_polling_profile.py:20](../../../alembic/versions/20260907_0013_serial_polling_profile.py#L20)
- 前端读取方案沿用现有设备管理入口。
  [DeviceCreateView.vue:202](../../../ruisheng-web/src/views/devices/DeviceCreateView.vue#L202)

**验收证据**

- 生产通知与5秒兜底驱动真实配置链。
  [test_serial_device_lifecycle.py:575](../../../tests/integration/test_serial_device_lifecycle.py#L575)

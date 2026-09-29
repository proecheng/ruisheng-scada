# RS485 多设备轮询：实施调研与证据

日期：2026-09-07。本文件保留实施前的调查与复现证据，下文“现有”“当前”均指调查时基线，不是修复后状态。用户批准后已完成本地实施及隔离集成测试；当前进度与验收结果见[主规格](spec-dynamic-rs485-device-polling.md)。本轮未连接目标机、未启动物理采集。

## 需求与资料

用户要求上位机可增减下位机，五台时分别发送五个地址的读取指令。设备列表是数量的唯一来源；无需手工维护十六进制指令或“设备总数量”字段。地址可以不连续，不能把文档中的九行当成最大支持九台。

`docs/Modbus读寄存器请求表.docx` 的九条请求已逐条离线重算CRC，均符合标准低字节在前。`docs/修正后485点位表.docx` 给出地址0..37；点位单位、倍率、DI编码及设备/固件适用性仍有未决，不由本功能替代校准。两份用户文档保留原样。

一轮五台的黄金请求向量：

| 从站 | 请求Hex |
|---|---|
| 1 | 01 03 00 00 00 26 C4 10 |
| 2 | 02 03 00 00 00 26 C4 23 |
| 3 | 03 03 00 00 00 26 C5 F2 |
| 4 | 04 03 00 00 00 26 C4 45 |
| 5 | 05 03 00 00 00 26 C5 94 |

应复用协议编码器按设备地址计算CRC，不硬编码五条帧。正常FC3响应是76字节数据、81字节整帧；异常响应为5字节，不能当成测量成功。现有正式网关Framer支持81字节；80字节限制属于独立B-11诊断脚本，本轮不修改它。

## 实际发现

1. `ruisheng-gw/src/ruisheng_gw/main.py` 仅启动时加载Registry和创建各设备poller；其后两个配置循环只刷新告警，无法发现新增设备或移除停用/软删设备。
2. `ruisheng-gw/src/ruisheng_gw/transport/serial_bus.py` 仅启动时建立 `_addr_map` 和会话。即使Registry新增D2，D2仍无会话，其有效响应也被忽略。
3. `ruisheng-gw/src/ruisheng_gw/scheduler/poller.py` 的总线锁在 `write/drain` 后释放，不等待下位机回复。同串口多个poller可以同时留下在途请求。
4. `ruisheng-api/src/ruisheng_api/api/devices.py` 和 `api/points.py` 固定写 `update_flag=1`；告警接口已采用自增版本和提交后通知，可以复用。重复改配置必须持续递增，而不是二值标志。
5. `ruisheng-shared/src/ruisheng_shared/models/devices.py` 的串口/地址唯一索引包括软删记录；API查重却排除软删。需新迁移统一为 `transport_type='serial' AND deleted_at IS NULL`。停用记录继续占地址，历史及旧设备号不删除、不复用。
6. 前端创建、编辑、启停和软删均已有入口。列表缺少从站地址列；普通PUT停用没有像专用enabled接口一样清除在线状态，应统一。
7. 模板复制中的 `dev_addr` 不是当前发帧地址来源，真正使用 `devices.modbus_addr`。模板分多请求复制存在部分完成风险，本轮不重做模板流程；无合法点位不得开始采集。

## 本机验证

运行现有六个单测文件：`test_poller.py`、`test_bus_lock.py`、`test_serial_bus.py`、`test_registry.py`、`test_supervisor.py`、`test_ingest.py`，均位于 `ruisheng-gw/tests/unit/`。结果：43 passed，2.48秒。它们通过不代表已覆盖动态多机生命周期。

另用内存Registry、MagicMock/AsyncMock串口writer和StreamReader做无网络复现：

- 五台各配置0..37点位后，并行调用现有poll_once，生成的五帧与上表完全一致。
- 没有注入任何响应，五个调用已全部完成，留下五个pending_read。这直接证实当前锁未覆盖完整事务。
- 模拟串口启动时仅D1，随后向Registry加入D2并注入CRC正确的D2响应：D2未建立会话，回调未收到数据。
- 合成81字节响应拆成40和41字节喂入Framer：首段不出帧，第二段后得到完整原帧。

以上是模拟，不是五台实物验收。当前只有1号现场设备有历史响应证据；2..5号未探测。

## 任务文件图

### T1 数据契约与迁移

- `ruisheng-shared/src/ruisheng_shared/models/devices.py`：增加非空 `read_profile`，默认 `point_groups`；允许 `zero_origin_38`，后者只允许serial。唯一索引排除软删，但不排除停用。
- `alembic/versions/20260907_0013_serial_polling_profile.py`：新增迁移，挂接实施时确认的当前单一head；旧数据保留默认读取行为。回退若存在复用地址冲突，应明确拒绝，不删除记录以强行降级。
- `ruisheng-shared/tests/test_models_devices.py`：模型约束测试。迁移执行只针对独立测试库。

### T2 API变更与广播

- `ruisheng-api/src/ruisheng_api/api/schemas/devices.py`：创建/修改/输出契约加入读取方案，并针对更新后的完整状态校验组合，不能只检查本次局部字段。
- `ruisheng-api/src/ruisheng_api/db/repositories/devices.py`：事务内配置版本自增、软删及同地址查重。由数据库索引兜底并发地址冲突，错误不泄露其他租户设备信息；不扩大API角色的跨租户读取权限。
- `ruisheng-api/src/ruisheng_api/core/config_changes.py`：从告警现有模式提取版本/通知小工具；自增在同一事务内，广播只在提交后，失败记录并由周期刷新补偿。
- `ruisheng-api/src/ruisheng_api/api/devices.py`、`ruisheng-api/src/ruisheng_api/api/points.py`、`ruisheng-api/src/ruisheng_api/api/alarms.py`：共用通知模式，设备增改停启删及点位增改删/导入均覆盖；普通PUT和enabled停用路径保持一致。选择zero_origin_38时，已有或新写入点位必须为FC3且完整寄存器跨度在0..37内。

### T3 配置对账

- `ruisheng-gw/src/ruisheng_gw/domain/registry.py`：完整设备集合增量对账，而不是只查询已知租户/已知设备；加载read_profile、点位、间隔、串口和地址。构建并验证候选快照后原子替换，保留未变设备运行状态；事务按快照/代际持有点位映射。沿用网关已有跨租户配置读取权限检查同物理串口的128台容量上限；超限不启用候选配置并报告应用失败，不按API调用者可见条数误判总线容量。
- `ruisheng-gw/src/ruisheng_gw/main.py`：复用现有5秒周期及Redis提示刷新；消息重复/乱序不得回退配置。配置应用与串口会话对账在总线事务边界完成。数据库失败保留最后成功快照并报告，不能把失败说成已生效。保留告警版本刷新与计数重置语义。

### T4 单总线完整事务

- `ruisheng-gw/src/ruisheng_gw/scheduler/serial_poller.py`：每个已配置串口一个调度任务，到期设备公平轮转；默认请求超时1000ms、轮内不重试、请求之间至少200ms静默。周期表示设备最小发起间隔；负载过高时延后，不并发、不补发积压来追赶。
- `ruisheng-gw/src/ruisheng_gw/scheduler/poller.py`：复用现有读组构建/编码，zero_origin_38产生唯一固定读取段，points稀疏也不改变帧；无合法点位不发包。保留其他型号按点位分组策略。
- `ruisheng-gw/src/ruisheng_gw/transport/serial_bus.py`、`ruisheng-gw/src/ruisheng_gw/transport/session.py`：事务关联接收、动态绑定与移除；串口、从站、FC、字节数及CRC均需匹配。当前FC的合法异常结束该事务但无测量入库；坏帧/未知地址不能提前释放它。停用/删除/改地址使旧配置代际失效，不将旧帧分配给新设备。
- `ruisheng-gw/src/ruisheng_gw/main.py`：串口不再同时启动旧per-device poller；不同串口可独立运行。关闭时取消并回收任务/会话/端口；断开须报告且不能继续使用旧writer。TCP原有调度保持不变。
- `ruisheng-gw/src/ruisheng_gw/ingest.py`：只允许本次事务快照对应的有效数据进入原有BatchWriter和发布链，不能让无pending串口帧走宽松回退；保持既有待提交修复。

串口改地址/复用的旧事务先结束，清理残帧并等静默后才绑定新代际。RTU响应没有事务号；当任意迟到帧与新请求同地址同长度时，协议本身不能证明它属于哪个时间点。实物延迟边界与地址复用验收仍须单独现场验证。

### T5 前端

- `ruisheng-web/src/api/devices.ts`：扩展读取方案的wire及页面类型并保留默认兼容。
- `ruisheng-web/src/views/devices/DeviceCreateView.vue`、`ruisheng-web/src/views/devices/DeviceEditView.vue`：串口设备提供“按点位分组”及“自研设备38寄存器”选择；保留现有地址输入、权限及错误提示。更改地址仅改变上位机配置，不下发修改硬件地址的指令。
- `ruisheng-web/src/views/devices/DeviceListView.vue`：通信信息含串口/从站地址，沿用已有增改停启删；不加一个独立于设备列表的数量输入，不改成新的首页。

### T6 验证

- `ruisheng-gw/tests/unit/test_serial_poller.py`、`ruisheng-gw/tests/unit/test_poller.py`、`ruisheng-gw/tests/unit/test_serial_bus.py`、`ruisheng-gw/tests/unit/test_registry.py`、`ruisheng-gw/tests/unit/test_ingest.py`：黄金五帧、非连续地址、81字节拆包/粘包、无响应及异常/坏CRC/错FC/错长度、迟到帧、零/一/五台、空启动后新增、连续变更、跨串口同地址不串数据、断开与取消清理、不同串口不互相阻塞。
- `ruisheng-api/tests/unit/api/test_devices_list.py`、`ruisheng-api/tests/unit/api/test_points.py`、`ruisheng-api/tests/unit/api/test_alarm_configs.py`：新契约与重复修改、提交后广播、两种停用入口、权限隔离、软删复用/停用占用、并发冲突。
- `tests/integration/test_serial_device_lifecycle.py`：专用测试库加五个模拟从站，走真实API配置、网关对账、收帧、实时/历史落库与API查询；新增到六台、停用回五台、删除到四台再恢复/复用地址。用不同原值明确断言数据所属设备和点位，校验旧测量历史保留。
- `ruisheng-web/tests/unit/api/devices.test.ts`、`ruisheng-web/e2e/devices.spec.ts`：五条记录显示、方案及地址编辑、停启删除、错误提示。浏览器mock页面通过不能代替上述真实API/网关集成测试。

保留既有非本任务改动。未运行的后端集成、迁移、浏览器及物理验收不得标为已通过；缺少测试环境要明确列出。

## 流程说明

仓库未提供 `_bmad/bmm/config.yaml` 或定制解析器，已读取技能默认配置，使用项目现有中文规格目录，不安装BMAD或改动项目配置。最初按bmad-quick-dev第二步形成单一用户目标规格；用户批准后进入本地实施，保留主规格中冻结的审批意图，现场操作仍须另行批准。

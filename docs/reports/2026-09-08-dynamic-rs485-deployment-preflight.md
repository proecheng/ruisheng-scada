# 多设备轮询部署预检

日期：2026-09-08。结果：目标在线且旧版本健康；新版本部署因跨数据库结构版本的升级流程尚未具备而暂停。未构建新候选、未上传、未切换服务、未执行数据库迁移或物理采集。

## 当次请求与边界

用户在本地多设备实现和测试完成后回复“按照建议执行”，继续候选构建、目标部署和逐台验收。预检发现既有签名升级规格明确将“允许schema变化/数据库恢复”列入另行确认范围，且冻结约束要求候选数据库版本与目标数据库版本一致。因此本轮仅进行受控状态读取、权益核验及只读健康检查，不移除保护条件或先手工迁移来绕过检查。

## 实际目标状态

目标观测时间：2026-09-08T07:13:43.3562223+08:00。

| 项目 | 观测结果 |
|---|---|
| SSH目标 | `lenovo@100.109.90.21` |
| 主机名 | `WIN-OAUCM8UQUGH`，与预期完全一致 |
| 站点标识 | `site-win-oaucm8uqugh`，与受保护固定身份一致 |
| 认证 | 本机固定OpenSSH路径、公钥认证、禁止密码和键盘交互、严格主机密钥校验 |
| 权益 | 受保护verifier确认 `remote-support`、`software-updates` 已授权 |
| 活动候选 | `deploy-20260907.1` |
| 活动源码提交 | `2150b5ee904760ce0af4483c201009b8744669a2` |
| 活动逻辑标识 | `sha256:961c3a28b4b1380c2e100c04b70f9b2a825ba2e2cda05960a77d1ee43218a810` |
| 数据库版本 | `0012_alarm_notification_runtime` |
| 五个服务 | `ruisheng-postgres/redis/gw/api/web`均运行，postgres/redis健康 |
| API内部健康 | database、redis、service、status均ready |
| GW内部健康 | batch、database、outbox、redis、service、status均ready |
| Web | HTTP 200 |
| 生产记录数 | devices=0、device_points=0、point_data_realtime=0、point_data_history=0 |
| 升级锁 | shared和legacy均不存在 |

升级Status查询操作ID：`c386ec4a-bd3d-4a6b-9ee8-d987b6f41a35`。使用既有干净操作端 `point-pipeline-operator-20260907` 的Status入口，返回 `ok=true/status=observed`。未调用Apply、Initialize或Recover。

上述记录数仅证明当前生产配置及采集表为空；不是设备未响应的结论，也不是新功能已部署。没有读取或输出环境密码、令牌、私钥或用户数据。权益核验沿用现有防时钟回拨状态机制，不修改grant或授权范围。

## 已确认的阻断原因

多设备实现包含新迁移 [0013_serial_polling_profile](../../alembic/versions/20260907_0013_serial_polling_profile.py)，新增读取方案字段并允许软删后复用串口地址。新网关要求该迁移head，不能直接替换到仍为0012的目标数据库上。

既有[签名全量升级规格](../superpowers/specs/spec-signed-full-release-remote-upgrade.md)要求同head升级，并明确规定跨schema及数据库恢复须另行确认。[目标升级器](../../tools/remote_full_upgrade/target-updater.ps1)在Apply时对head不一致抛出 `schema_head_changed`；现有恢复路径恢复镜像与环境，不能冒充数据库恢复。此次为源码与目标实际版本核对得到的阻断，没有上传一个已知不兼容候选来触发失败。

本地工作区还有其他任务的未提交改动。后续发布应复用独立干净发布工作树，精确纳入多设备实现并保留目标上已有采集修复，不能打包整个脏工作区或回退用户改动。

## 需要确认的下一步

建议批准仅面向 `0012_alarm_notification_runtime -> 0013_serial_polling_profile` 补齐受控迁移与失败恢复，不解除任意跨版本升级保护：

1. 先完成明确的迁移、服务停写、备份可恢复验证、失败状态和恢复边界设计及隔离测试。
2. 保留签名验证、固定站点身份、双锁、操作ID及审计；签名候选与允许的迁移边必须绑定。
3. 从独立干净发布工作树构建候选，目标备份验证通过后才执行获准迁移与服务升级。
4. 保留旧候选、配置及数据库备份；不删除卷、不修改信任密钥、不绕过现场采集门禁。
5. 先做目标软件健康及隔离库验收，再对已经确认的1号实物设备设计有界读取验证。2..5号不能仅因模拟测试通过就假定存在或自动扫描。

正式连续采集仍需点位含义、单位、倍率及设备适用性核实。上一轮[本地验收报告](2026-09-07-dynamic-rs485-local-acceptance.md)中的模拟多设备通过结论保持原范围，不改写成目标机已部署或已通过实物验收。

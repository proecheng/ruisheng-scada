# 点位数据类型：有符号字节 / 无符号字节

## 当前实现

隔离工作区 `C:\Users\admin\AppData\Local\Temp\ruisheng-point-type-hotfix-20260924` 已增加“有符号字节”和“无符号字节”选项，并扩展 API 类型校验、网关轮询白名单和入库解码。候选实现将一个 Modbus 16 位寄存器的低 8 位解释为字节：有符号范围 -128～127，无符号范围 0～255。

## 验证

- API/GW 定向测试：71 passed。
- 网关轮询测试：11 passed。
- Ruff、mypy：通过。
- Web 类型检查：通过。
- Web 单元测试：25 个文件、146 passed。
- Web 生产构建：通过。

## 部署状态

目标机语义尚未确认，因此没有切换现场。API 目标预检通过，随后完整 API 回归 684 passed、8 skipped；Docker 构建在本地被有界中止，未产生新镜像，也未修改目标。当前目标仍为 API `ruisheng-candidate/api:deploy-20260922.1`、Web `ruisheng-hotfix/web:02c8f313cc86`，维护锁已释放。

复核证据：[point-type-deploy-preflight-20260924.json](../tmp-test-logs/field-acceptance-20260916/snapshots/point-type-deploy-preflight-20260924.json)。状态记录已写入 `observation-state.json`，修改前备份为 `observation-state.pre-point-type-deploy-20260924.json`。

## 待确认

请确认“有符号字节/无符号字节”具体含义：

1. 一个寄存器的低 8 位：`0x00FF` 解释为 `-1` 或 `255`；
2. 整个 16 位寄存器：`0xFFFF` 解释为 `-1` 或 `65535`；
3. 一个寄存器的高 8 位。

确认后再完成最终解码和受保护部署。

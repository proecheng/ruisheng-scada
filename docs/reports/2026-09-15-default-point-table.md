# 默认 RS485 点位表及目标机名称应用（2026-09-15）

已根据 `docs/修正后485点位表.docx` 为零基地址 0～37 生成默认 CSV，并将目标机 WIN-OAUCM8UQUGH 的 DEV001「1号泵站」现有 38 个点位改为对应物理量名称。目标活动软件仍为 deploy-20260915.1 / 07ea7c31116ddc426bee881c1c5b0831bff33f9d，无需重新发布软件。配置版本由 14 增至 15。

## 交付文件

- `docs/point-tables/default-rs485-38-points.csv`：UTF-8 BOM，系统现有 15 个导入/导出字段，38 行。
- `docs/point-tables/default-rs485-38-points.mapping.json`：逐地址来源说明及哈希。
- `docs/point-tables/README.md`：适用协议、使用方法、原值口径及文档计算说明。
- 目标系统目录 `C:/Ruisheng/site/point-tables/` 和用户桌面 `C:/Users/lenovo/Desktop/点位表/` 均保存上述三份文件，哈希一致。
- 最终页面截图：`docs/screenshots/2026-09-15/realtime-default-point-names.png`。

示例：地址 0 → 开关量输入 DI；地址 1 → 开关量输出 DO；地址 20 → B相电压谐波含有率 THDU；地址 35、36 → 环境温度(DHT11)、环境湿度(DHT11)；地址 37 → 漏电流。名称统一追加「(原始值)」。31 是第 4 路 NTC 温度，32～34 为预留，遵循用户关于温湿度地址的更正。

## 配置变更及数据保留

现有 CSV 导入是新增，不是覆盖更新，因此没有在已有设备重复导入或删除重建点位。管理员维护事务锁定 DEV001 和其 38 点，逐项核对修改前 API 快照，再一次性更新 point_name、user_point_name。点位 ID、地址、功能码、从站地址、数值类型、倍率、单位、偏移、显示及阈值保持；updated_at 由数据库触发器自动推进。设备配置版本遵循现有通知版本语义增加一次，网关通过既有周期刷新恢复采集。未变更账号权限、部署候选、容器、采集协议、串口或泵控制。

目标备份：`C:/Ruisheng/tools/default-point-names-03ad4d6bcc334161bf907af107892639/`，含 points-before.csv、points-before.json、applied.json（完整前后数据库点位行）及事务脚本、输出。不要重跑此次应用脚本；重跑会被原配置校验阻止。

## 实际验证

- 默认 CSV 通过产品 `_parse_csv_row` 和 `zero_origin_38` 合约验证，38 个唯一地址；真实目标导出 CSV 的所有 15 字段逐点对应默认表（数字字符串 1 与 1.0 按同值处理）。
- 两次真实 Chrome/SSH 观察，各 40 次页面快照、38 卡片保持，分别收到 7、6 个真实采样轮次；逐卡名称、数值、采集时间匹配真实 WebSocket 帧，无 NaN 或页面异常。
- 点击地址 20、35、36 的卡片，历史图表和表格均可见，每项查询显示 1000 行，并采用新名称。改名前后 API 历史快照中 811 条重叠记录的完整字段逐条相同，涉及 point_id 21、36、37。
- 最终独立导航验收通过：进入点位配置并等待 38 行表格，返回实时页、重新加载后仍显示 38 张正确名称卡片、采集间隔 5 秒。
- 12:53:47 数据库独立核验：DEV001 online=true、loss_count=0、38 点，最新值年龄 2.18 秒，历史 477166 条；网关原容器保持、重启计数 0。原值 35=290、36=490，按文档为 29℃、49%RH。

本次浏览器在本机经 SSH 访问真实目标站点，没有假称控制目标 Edge 鼠标。

## 数值和采样限制

本次只命名，倍率保持 1、单位空，避免已存原值历史与新工程值混合。温湿度按文档可除以 10；功率因数、频率及 DI 等仍存在文档与实测矛盾，默认 CSV 不推断倍率或符号。不能把原值直接标注为工程单位，也不将此表宣称为所有设备通用模板。

观察中仍有约 9.8～10.1 秒采样间隙，其间页面保留上次数据，随后恢复；数据库最近轮次也有同样缺样。此限制与之前串口回包超时现象一致，本次不宣称每个 5 秒轮次均收到物理回包。新的频率/谐波原值也仍需硬件协议资料核对，本次仅使用文档名称。

## 过程异常与最终状态

巡检账号 rs_admin 的点位 PUT 因缺少 CA 0x02 返回 403、没有修改；随后使用用户此前已授权的 SSH 管理员维护通道，并保持账号权限。首次维护脚本在启动数据库进程前遇到 PowerShell 5.1 不支持 StandardInputEncoding 属性；后续改为 UTF-8 BaseStream 输入。首个数据库事务因 updated_at 触发器被严格全字段比较拒绝并回滚，确认触发器后仅允许该时间元数据差异。第二个事务于 12:51:00 成功 COMMIT，但外层按单行提取 JSON 回执失效；独立按完整 UTF-8 多行 JSON 读取确认提交并保存 applied.json，没有重复更新。

前两轮浏览器测试在尚未完成进入点位配置的路由转换时提前后退，误回设备列表，导致末尾卡片断言失败。此前实时/历史检查均通过；修正验收等待实际配置路由和 38 行表格后，聚焦导航复验通过。没有因此修改产品源码。

最终证据：

- `tmp-test-logs/finalize-default-point-names.remote-f20d151bf8f9400a88037ab087963b23.json`
- `tmp-test-logs/default-points-navigation-verify-9cb38f6a-f81d-4519-b1af-c02bae509bd9/result.json`（最终导航通过）
- `tmp-test-logs/default-points-verify-35b28010-63b8-4e33-b87a-b9833eb8214d/result.json`（40 次实时/历史通过，末尾导航脚本失败）
- `tmp-test-logs/default-points-verify-c3eee51c-6537-4811-b0b1-a1f0f55cb131/result.json`（第二次实时/历史证据）
- `tmp-test-logs/default-point-history-preservation.json`
- `tmp-test-logs/inspect-serial-collection-20260914.remote-c173f83d3a2d4fa8890dd8a318bba541.json`

应用与验证已结束，没有后台部署、配置修改或浏览器验收正在执行。

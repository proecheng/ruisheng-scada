# 第三轮审查服务中断与恢复入口

记录时间：2026-09-08 23:16（Asia/Shanghai）。本记录是进度和阻塞证据，不是审查通过结论。

## 当前结论

第三轮独立审查尚未完成。两名已启动的审查代理均被模型服务以 HTTP 403 拒绝继续，原因是额度不足；不能把未返回 findings 当作零问题或通过。尚未执行本轮本地提交、签名构建、候选上传、生产迁移、服务切换或实物采集。

服务分别报告 blind 上下文约 81,862 tokens、最低预扣约 ¥0.409310，edge 上下文约 85,539 tokens、最低预扣约 ¥0.427696，而当时可用余额为 ¥0.112118。该信息只解释此次请求拒绝，不是完成全部后续工作的报价。没有通过自动重试、切换低能力模型或取消审查来宣称放行。

- `bounded_blind_r3`：服务中断，未形成最终报告。
- `bounded_edge_r3`：服务中断，未形成最终报告。
- `bounded_acceptance_r3`：此前因代理线程上限未能启动；没有验收审查结果。

沿用 `bmad-quick-dev`。其 `step-04-review.md` 要求三名无对话历史的审查代理；无法运行时生成三份独立提示文件并暂停。这次是模型服务无法继续完成审查，并非用户未授权部署，也不是自动审批拒绝。配套提示见本目录 13、14、15 号文件。恢复服务后应使用新的、与主会话相同能力且无对话历史的审查会话；原长上下文失败会话直接续跑可能再次触发预扣限制。

## 已冻结的源码和通过证据

- 发布工作树：`C:\ProgramData\Ruisheng\publisher-build\bounded-schema-multidevice-20260908`。
- 发布分支：`codex/bounded-schema-multidevice-20260908`；当前 HEAD：`5d83607bc6d5bf90234ac713aa5dab6b96eb3461`。
- 规格 baseline：`d839330a91cdf7cae3f4c30aff9396fb99a47120`；审查循环仍为 3，状态仍为 `in-review`。服务故障不增加规格循环计数。
- 冻结差异：`D:\江苏润盛\tmp-test-logs\bounded-review-r3-n3TAnU\implementation.diff`，81 个文件、16,425 行，SHA256：`e565e348793ad14d3c4028d4ce890d140f28ea96f24c754b1194a3c93d9348c0`。
- 相邻 `manifest.json` 保存全部文件摘要；恢复检查时 81 个文件全部匹配，`git diff --check` 通过。本次新增的恢复说明和提示只写入根工作区，未同步修改冻结发布工作树。
- 主组合 XML：`D:\江苏润盛\tmp-test-logs\bounded-release-r3-5d8aab694ec14d8fa736b44365d08f66\results.xml`，395 项、394 通过、1 失败、0 errors/skips，9814.928 秒。
- 同源码短目录补测 XML：`D:\江苏润盛\tmp-test-logs\bounded-ps5-short-path-9988d7856c39491b8b6d00e86538cb48\results.xml`，1 项通过、0 failures/errors/skips，488.847 秒。

唯一主组合失败为 PowerShell 5.1 完整 Apply 的长测试回执原子替换路径问题。真实函数对照在 213 字符回执、264 字符替换路径复现 `DirectoryNotFoundException`；短路径及 PowerShell 7 通过。补测只重定向取证目录，未改产品源码、测试断言、SQL 或迁移输入。准确结论是同版本全部 395 项均有通过证据，由主组合 394/395 与补测 1/1 组成，不是单次全绿；原 PowerShell 7 偶发回退的唯一原因仍未证明。

关键文件 SHA256：

| 文件 | SHA256 |
|---|---|
| `tools/remote_full_upgrade/target-updater.ps1` | `9e449fd98ae0f8bb51ebac8f9fe9813b12a1fb2dc71bf8a420672b3c7e6753e7` |
| `tests/tools/test_remote_full_upgrade.py` | `3070c28e6275f3b1599ec89f22917462ae973144c5ebedf22afc5a83305fa52d` |
| `tests/integration/test_schema_upgrade_recovery.py` | `8c775e17988787a86269d83578d86b79efdb161e16347dda8ceeb3f4aa3f06ad` |
| `alembic/versions/20260907_0013_serial_polling_profile.py` | `df20fff11e1b8ea04ef4ce69126c29021e221685ad27a138bbfafe10143ac5d1` |

主组合 204 个、补测 13 个独有测试容器均已有停止及身份检查证据；备份、卷和日志保留。本次没有重新启动这些资产或删除任何材料。

## 此次目标和发布机预检

目标只读 Status 操作 ID：`1f38f0d8-e46e-4716-a4d9-0ef3d87933ab`，通过既有 `point-pipeline-operator-20260907` 的 Status 入口返回 `ok=true / observed`。SSH 使用固定 Windows OpenSSH 路径、公钥方式、严格主机密钥检查，且本机 SSH 的 Microsoft Authenticode 签名有效。

23:10:09 +08:00 的进一步观测：

| 项目 | 结果 |
|---|---|
| 目标 | `lenovo@100.109.90.21`，主机 `WIN-OAUCM8UQUGH` 匹配 |
| 站点 | `C:\Ruisheng\candidates\site-deploy-20260831.1`，路径长度 45 |
| 活动候选 | `deploy-20260907.1` |
| 活动源码 | `2150b5ee904760ce0af4483c201009b8744669a2` |
| 维护锁 | shared、legacy 均不存在 |
| SSH 管理员令牌 | 有效 |
| 权益核验 | 固定 verifier 授权 `remote-support`、`software-updates` |
| 五服务 | postgres、redis、api、gw、web 均 running；postgres/redis 的容器健康状态 healthy |
| API/GW/Web 内部健康 | 此次没有重新探测，不能把 running 称为完整健康通过 |
| 新保护安装回执 | 尚不存在 |
| 持久维护标记 | 不存在 |
| 润盛计划任务名称 | `Ruisheng-Docker-Start`、`Ruisheng-Serial-Hardware-Attach`；尚未重新执行完整入口语义核验 |
| 目标 C 盘可用空间 | 127,676,870,656 bytes |

权益检查沿用现有 verifier，会按其协议维护受保护的 last-seen 状态；没有更改 grant、租约或信任锚，没有启停服务或改变业务配置。此次未重新查询数据库 head 或数据行数，不能把 07:13 的 0012/空表观测冒充 23:10 的实时结果。

发布机检查时 C 盘 4,214,177,792 bytes、D 盘 113,708,027,904 bytes，达到既有构建门槛；正式构建前仍由脚本重新检查。

## 服务恢复后的顺序

1. 核对冻结差异及 81 个文件摘要；通过三份提示启动独立审查。收齐、去重并分类 findings，按 step-04 处理。服务中断无需单独重跑已完成测试；源码修订、失败或新的未决问题才触发相应验证。
2. 完成审查后读取此前尚未执行的 `step-05-present.md`，结合本会话已有授权处理实际检查点，不反复索要已经给出的权限。
3. 限定本地提交，使用 `tmp-test-logs/build-bounded-candidate.ps1` 构建新 API/GW/Web 并签名，保留精确相同 PG/Redis 镜像；不推送。
4. 使用 `tmp-test-logs/verify-bounded-candidate-apps.py` 对本次正式候选做隔离真实应用启动/健康验收，停止独有资产并保留存储。
5. 重新核对目标实时状态，受控安装启动保护并验证实际入口，再 Plan/Apply、验证实际备份还原、数据库 0013、五服务身份/健康、审计和数据保留。
6. 仅在原授权范围内做实物有界验收：目前只有 1 号设备有物理证据，2～5 号仅模拟；不绕过 B11 旧诊断 80 字节上限，不启动任意扫描或连续实物采集。

生产数据库回灌/降级、其他迁移边、信任密钥变更、推送/PR/合并、删除数据/卷、系统重启均不包含在本次恢复动作中。

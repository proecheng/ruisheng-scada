---
baseline_commit: a78c522b6879e4ed54a7f369dfcdb0083001a071
---

# 有界升级应用启动与历史 Compose 失败恢复

2026-09-11 现场操作 4d786d05-fb02-4e68-927a-3eec4edaa823 已完成批准的 0012→0013 迁移。随后 Compose start 遍历 depends_on，重启旧镜像的 migrate 容器，旧镜像因不认识 0013 退出 255，三项应用均停留在 created。完整取证：tmp-test-logs/bounded-deployment-failure-ffeb051ee8ca40a18e42126f7a407f05.json。

修复范围仅为升级器应用启动及这类可辨识旧故障的 Recover。签名候选 deploy-20260911.1、镜像、0013 迁移、原 900 秒测试限制、维护锁、数据和备份均保持。用户已授权受控升级、必要修复和恢复；不执行生产数据库 restore/downgrade，不扫描硬件。

验收要求：

1. 仍通过 Compose up --no-start --no-deps 准备三项应用，核验镜像、停止状态和 restart=no，复查启动保护与原角色回执后，只使用核验所得不可变容器 ID 启动这三项应用；依次启动，确保 API 先于 Web 完成容器启动。不得重新启动旧 migrate 或其他依赖。
2. 新的恢复协调只适用于 journal 明确记录的 recovery_failed、production/compose/application_start_intent，且应用启动已被尝试。原始 docker_command_failed 必须由完整校验的审计链中唯一一条、绑定同操作/原因/候选、发生于 intent 及旧迁移器退出之后的 upgrade_apply_failed/failed 记录证明；最新恢复错误不得改变原始错误的判定。缺失、歧义、损坏审计及原始 timeout 均拒绝；仅发生于历史 Recover 的 Compose 失败不在此兼容范围。role_restore、restart_restore、snapshot 和其他命令保留既有拒绝逻辑。
3. 清除这个已观察的旧启动 intent 前，必须持有锁并验证数据库身份、完整备份恢复回执、准确 0013 结构、所有迁移容器停止、两项应用角色均 NOLOGIN、无未知数据库连接。
4. 本操作新迁移容器必须成功退出，完成时间早于启动 intent。恰好一项已登记的旧镜像迁移容器必须在 intent 之后启动、失败退出，项目/服务身份一致。三项新应用必须仍为 created、从未启动、PID 0、restart=no、镜像和项目/服务身份正确。
5. 将原 intent、观察到的容器 ID 和旧迁移退出结果保留于 journal，并写入审计。任一门禁失败不得清除 intent。journal 写入失败必须恢复内存中的原 intent。协调后继续原有完整 Recover 与最终提交检查，不提前开放角色或服务。
6. 真实 Docker 回归应覆盖生产依赖图及旧迁移容器，证明新启动不重启它；另复现旧 Compose 故障后使用真实 Recover 完成前向恢复。边界测试覆盖拒绝错误作用域、超时、已启动应用、错误镜像、失败备份/结构/锁/角色/写入等情况。
7. 应用升级包保持不可变，修复升级器在独立本地工作树中验证和审查，恢复执行须绑定该升级器摘要、原候选身份和原操作 ID。部署后另行核验备份、角色、数据、配置、健康、锁与审计。

## Review Findings

- [x] [Review][Patch] 按 API → Web 依赖顺序启动已核验 ID，避免 nginx 解析尚未注册的 api 名称后退出。
- [x] [Review][Patch] 使用原始 Apply 失败审计证据，避免恢复重试覆盖 error_code 导致永久拒绝，或使原始超时被误判为可协调失败。
- [x] [Review][Patch] 将完整协调观察写入审计，包含原始 intent、容器 ID、旧迁移器退出码及对应原始失败审计摘要。
- [x] [Review][Patch] 读取原始失败审计前验证该文件的限制权限及链接状态，不能仅依赖可重新计算的 SHA256 链。

三路复审均无剩余可执行发现。最终实现 8 项重点测试在 PowerShell 5.1/7 下通过，完整工具回归 189 项全部通过，真实 Docker 恢复验收 2 项全部通过（1837.60 秒），均无失败或跳过。最终执行器提交 7a672a914283b10163dff97706027d73e6ced8d6、升级器 SHA256 b432944533ffbd2ee075c98959fdb56cf501fa90dc2b8f4433ae91672c18e5c5。

2026-09-11 14:20 对原操作派发一次 Recover，14:23 成功 `committed/completed`。完整旧故障观察保留，活动版本提交为 deploy-20260911.1；14:23:58 和读取后的 14:33:59 两次独立部署核验通过，确认备份、角色、环境、服务、锁和审计正常。证据见 [部署接续报告](../../reports/2026-09-11-deployment-continuation.md)。

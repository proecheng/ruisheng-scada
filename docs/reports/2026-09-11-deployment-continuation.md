# 多设备版本部署接续（2026-09-11）

用户要求按“修复预检、受控升级、真机验证、交付收尾”顺序执行。本报告时间均为北京时间；只把已取得证据的步骤记为完成。

**当前结果：晚间日期输入修复已部署为 deploy-20260911.2 / 数据库 0013，登录、14 页面、10 类交互和普通用户桌面启动验收通过。** 日间 deploy-20260911.1 上的 1 号设备单次复验为 5 次有效读取、无重试，47 项收尾核验通过；该证据保留其原版本绑定。更多实物设备、正式点表、校准和持续采集尚待确认。下文保留失败、修复和最终验收的完整过程。

## 预检错误定位与修复

10:46只读确认目标WIN-OAUCM8UQUGH仍为deploy-20260907.1、数据库0012，五服务运行，API/GW ready、Web200，设备/点位/实时/历史计数0|0|0|0。启动保护回执存在，无维护标记。证据：tmp-test-logs/bounded-target-health-75d1987d94974613bb7637cecd733144.json。

真实SSH引导设置Console.InputEncoding为Text.Encoding.UTF8。在Windows PowerShell 5.1中，Process.StandardInput的文本写入器可在输入流开头写入UTF-8 BOM；包装器从Console.In读取后，该字符进入JSON字符串，ConvertFrom-Json拒绝解析。

10:50目标只读取证确认首字符为U+FEFF（65279），关闭底层流的提案不能解决问题。10:51同一输入在消费一个开头BOM后JSON解析成功，实际Docker查询返回0012_alarm_notification_runtime。两次均changes=false、modbus_tx=0。证据：tmp-test-logs/bounded-stdin-diagnosis-f0dd929acab14b9db2fb38695265f67a.json及bounded-stdin-diagnosis-a65b9310b9734a2abecf7f99e65b1b51.json。

正式修复只在长度门禁之后、JSON解析之前消费一个开头U+FEFF，不修改输入中间字符、迁移白名单、进程Job、锁、超时或恢复语义。根工作区与独立发布树同步此局部修改。

回归先在原代码增加真实SSH编码环境：4项参数用例中Windows PowerShell的UTF-8用例失败、其他3项通过，见bounded-stdin-red.xml。关闭底层流的试验失败记录为bounded-stdin-green.xml，未采用。最终修复的14项真实进程/参数/父进程死亡/超时/失锁测试通过，见bounded-stdin-bom-green.xml。

三路独立审查（Blind、Edge、Acceptance）均无可执行发现；审查范围为发布HEAD 0a7bbf3之后的两个文件。提交检查通过。后续提交、签名候选与测试进展见下文；目标升级仍待必要验收通过。

固定实现摘要：

- tools/remote_full_upgrade/target-updater.ps1：9503242661b2cca6f1f91747556b47255481f0e831e469e719339ae4f8cfc5ae
- tests/tools/test_remote_full_upgrade.py：8ed991a5b14a986e425ff2b5f3ef0283857c2e01a6fee6fd4db6eaecb460d538
- 批准迁移0013：df20fff11e1b8ea04ef4ce69126c29021e221685ad27a138bbfafe10143ac5d1（保持）

## 后续执行

10:56只读核实目标已有PowerShell 7.6.5及Windows PowerShell 5.1.26100.9444；本机pwsh为7.6.4。此次错误来自升级入口使用5.1时的输入编码契约，不是缺少PowerShell 7。未安装/升级目标运行时或切换入口。证据：tmp-test-logs/target-shell-versions-3cee21623f1843e391afd658b416d288.json。

真实数据库集成首轮4项因本机Docker未运行而跳过（bounded-r9-integration-5b02eb95503f436c89027a7529dd4047），不能算通过。启动Docker时遇到旧AF_UNIX通信文件无法清理；已将只含零字节通信文件的运行目录改名保留后重启本轮启动的Docker进程，未删除镜像、容器、卷或设置。保留目录为本机AppData/Local/Docker/run-before-20260911-*及AppData/Local/docker-secrets-engine-before-20260911*。数据库测试需在Docker健康后重新执行。

10:59目标只读确认已登记FTDI适配器仍映射到/dev/ruisheng-rs485，未开串口、未发送Modbus。证据：tmp-test-logs/bounded-r9-usbipd-presence-e191e5cb01834016b046c275e5083632.json。

完整测试通过后制作限定本地提交和新的签名候选；重新验证候选应用、目标状态和启动入口，再执行同操作ID的Plan/Apply与部署后核验。现场先沿已限定的1号设备诊断配置执行一次有界读取；2至5号设备及正式点位/连续采集结论仍待实物和点表证据。

全新安装PostgreSQL启动竞态仍待独立修复及真实空卷冷启动验证，不能以已有数据库升级验收替代。

## 发布准备进展

11:06使用固定修复源码与上一签名候选执行只读Plan诊断，结果planned，版本、平台、备份空间、Docker内存/磁盘门槛均通过；此操作只证明预检问题已解决，不作为新候选的最终部署Plan。证据：tmp-test-logs/bounded-fixed-plan-5c954e4d640442b3b4123fdb66d53d25。

完整工具183项通过，728.32秒，无失败/跳过，源码摘要前后不变；证据：tmp-test-logs/bounded-r9-tools-e23ceec5e71f4a8196a1cb24f1a975ca。三路审查及提交钩子通过后，只将上述两个文件提交于独立发布树，本地提交a78c522b6879e4ed54a7f369dfcdb0083001a071，未推送。为并行推进可逆准备，开始构建deploy-20260911.1；目标Apply仍等待全部必要测试及新候选应用验收通过。

本机Docker已恢复running；真实数据库测试重跑于tmp-test-logs/bounded-r9-integration-e66ee39e59f4403c8c88db62bb02eba9。其源码仍为固定摘要，不因创建Git提交改变；尚未将运行中的结果记为通过。

新签名候选deploy-20260911.1已构建完成，位于C:/ProgramData/Ruisheng/publisher-output/deploy-20260911.1，源码提交a78c522b6879e4ed54a7f369dfcdb0083001a071。目标尚未上传/切换。11:09:47以修复后的升级器重新核验既有启动保护与255项启动入口，installed_guard.verified=true、rejected=[]，启动器与回执摘要保持，未重新安装。证据：tmp-test-logs/bounded-r6-target-entrypoints-a04e4b98b5b746d881945399d707aa02.json。

候选逻辑身份sha256:9a4a6b959b5ec97dcaa554355ec6a4200b60d85c2733e88b29921952b671ac40，MANIFEST SHA256 580eca680e15efb9ecc5bea82de4b6cb825ad955737717a30854f85224ff74ae。

### 实际集成首轮失败保留

e66ee39e59f4403c8c88db62bb02eba9首项Windows PowerShell完整Apply在原900秒限时到达后失败，setup 201.87秒、call 901.68秒；因此其余3项未执行。持久阶段为migration_verified，错误字段为空、docker_intent为空、application_start_attempted=false，不能记作升级完成或恢复通过。最后观察到Compose调用；新候选构建与该测试曾并行，但尚不能把超时唯一归因于资源竞争。

本轮14个容器已核验全部停止、身份和无宿主端口匹配，卷、备份和日志保留，见该证据目录owned-assets.json。发布构建已结束，将在无并行构建/应用启动任务时，以完全相同源码和原900秒限制重跑相同4项。未降低任何验收条件，目标仍未升级。

无并行构建/应用启动的复测目录为 `tmp-test-logs/bounded-r9-integration-55478c31cec84cce8927f53ee32b6e2e`。4 项全部通过，总计 2117.86 秒，无跳过，源码摘要前后保持。Windows PowerShell 5.1 完整 Apply 用例 call 744.49 秒，PowerShell 7 完整 Apply 用例 call 389.79 秒，均在原 900 秒限制内，最终 `committed/completed`。迁移超时后保持隔离并在迁移结束后恢复，以及停库/缺失应用/重复恢复两项也通过。43 个相关容器身份核验通过、均已停止、无宿主端口，卷和日志保留，见同目录 `owned-assets.json`。这些结果不抹去首轮超时记录，也不足以单独证明首轮超时的唯一原因。

12:02:59 刷新目标只读健康：仍为 `deploy-20260907.1` / `0012`、五服务运行、API/GW ready、Web 200、计数 `0|0|0|0`，无维护标记。证据 `tmp-test-logs/bounded-target-health-afd8c23fb8cf4f1db2cc1bbfe6c61028.json`。

### 独立冷启动修复完成

新工作树 `C:/ProgramData/Ruisheng/publisher-build/postgres-tcp-readiness-20260911` / 分支 `codex/postgres-tcp-readiness-20260911` 基于本地提交 a78c522b6879e4ed54a7f369dfcdb0083001a071。两份生产 Compose 的 PostgreSQL 健康检查增加 `-h 127.0.0.1`，以等待最终 TCP 服务。51 项生产配置测试通过，见 `tmp-test-logs/postgres-tcp-compose-20260911.xml`。

真实临时初始化服务回归：原配置两项均因临时 Unix socket 服务被误判为 healthy 而失败，证据 `tmp-test-logs/postgres-readiness-3060fecf1b3e406e83ce74315a0e10c2`；相同测试在修复配置下两项通过，62.12 秒，源码摘要前后保持，证据 `tmp-test-logs/postgres-readiness-df7dcfb09dfd493e94faeff767693521`。

首次回归的 `cf73b7b50eb340bbb1003351dd778985` 保留了一项预期失败和一项夹具失败。后者已查明为本机 Docker 默认网段池耗尽，原始错误保存在该目录 `network-diagnostic.log`。测试改为从明确的私有网段选择未占用的 /28 内部子网，不删除旧网络或数据；修复夹具后重新取得上述完整红/绿结果。

空卷全栈单次 Compose 启动验收通过，证据 `tmp-test-logs/postgres-cold-start-903dc6a3538d478da29db83d5baae7aa/result.json`：使用已验签候选的真实镜像，仅替换经过范围核对的 PostgreSQL 健康检查，无提前启动基础设施或手动等待 TCP。迁移退出 0，数据库 0013，API/GW ready，Web 200、资源可访问、管理接口 ACL 返回 403；内部网络无宿主端口，容器全部停止、存储保留。该结果验证修复后的配置，不将原签名候选标为已含冷启动修复。

修复和测试已同步根工作区，并独立提交 `6508209bc9874c543d76aacc49a24bfc29a3b9c1`；提交钩子通过，工作树干净，提交源码与通过测试的摘要一致，未推送。两份 Compose 摘要分别为 `6f756039bd2f9934bf314ada5a968492b19ba2482a6807af0c78ce24b79cf60e` 与 `232ccd8a8d3301361a1b3c60d2bca47bc8e776563cabfa0621ff1fbbb2e87704`。现有签名候选及其发布工作树保持不变。

### 新候选最终预检与升级执行

原签名候选应用验收通过，证据 `tmp-test-logs/bounded-apps-e0fbc47be53042dfbb0937be6d79ab99/result.json`，保留其 `infrastructure_tcp_ready_then_applications` 验证边界。12:04:54 目标既有启动保护及 255 项启动入口复查通过，零拒绝项，启动器和回执摘要保持，证据 `tmp-test-logs/bounded-r6-target-entrypoints-6169827630eb4e8fbae6a275ac9eee84.json`。

最终 Plan 于 12:07:13 返回 planned，使用已验收候选、固定提交和身份；资源、兼容性、锁状态均通过。操作 ID `4d786d05-fb02-4e68-927a-3eec4edaa823`，证据目录 `tmp-test-logs/bounded-deployment-20260911-a78c522`。同操作 Apply 于 12:08:01 启动；12:46:39 只读观察确认上传完成、目标 journal 为 `candidate_staged`，错误字段为空，尚未记录迁移阶段。证据 `tmp-test-logs/bounded-deployment-progress-2592e3cc86be4866b5db5f6fdc96a0b1.json`。连接使用 Tailscale 香港中继；只读诊断未建立直连，未修改网络设置。

12:53:02 Apply 返回 `recovery_failed / docker_command_failed`，阶段 `application_start_intent`。此前已完成并记录迁移验证；备份 `restore_verified=true`、快照已停止。应用启动的 production `docker_intent` 仍保留，旧版本指针尚未切换、环境已切换。原始 `apply-result.json` 和 stderr 已保留，后续 Status 确认该状态。正在只读取证，不手动清除 intent、不盲目重放 Apply、不恢复或降级生产数据库。

### 应用启动故障修复与受控恢复准备

只读取证 `tmp-test-logs/bounded-deployment-failure-ffeb051ee8ca40a18e42126f7a407f05.json` 确认：新迁移器已退出 0，数据库准确为 0013；Compose start 遍历依赖后重启旧 migrate，旧镜像因不认识 0013 退出 255。三项新应用均为 created、PID 0、restart=no，API/GW 角色为 NOLOGIN，PostgreSQL/Redis 正常。目标尚未恢复，尚未发送硬件读取。

修复在独立工作树 `C:/ProgramData/Ruisheng/publisher-build/bounded-application-start-reviewed-20260911` 完成，本地提交 `f874d507b83875186a68697b3656fcf610e4a289`，提交钩子通过、未推送。升级器摘要 `b432944533ffbd2ee075c98959fdb56cf501fa90dc2b8f4433ae91672c18e5c5`。根工作区同步三份修改，冻结候选及 a78c522 发布树保持。应用仍由 Compose 仅创建；验证后按 ID 依次启动，确保 API 在 Web 前启动，避免触发旧 migrate。

三路代码审查发现并修复了启动顺序、原始错误来源、审计证据完整性及审计文件权限门禁问题，复审无剩余发现。恢复只接受完整有效审计链中的唯一原始 Apply 失败；随后核验已验证备份、准确数据库结构、停止的迁移器、NOLOGIN 角色、未启动应用及无未知连接。完整观察同时写入审计和 journal 后才清除已观察的旧 intent，继续正常 Recover。超时、缺失/损坏审计和其他类型待决操作继续拒绝。

13:27 只读审计证据 `tmp-test-logs/bounded-application-failure-audit-a6e45f8d08a44386bc547a0531691f65.json` 确认原始 `upgrade_apply_failed/failed/docker_command_failed` 记录发生于 12:51:14，摘要 `c2a7818b63bbe23a786bab7233eaced170693e6f62a8d0bff152b71bf77cf639`，晚于旧迁移器退出，满足新条件的时间与来源要求；仍需由实际 Recover 验证完整链和现场全部门禁。

第一版修复的 187 项工具测试通过（858.71 秒），见 `tmp-test-logs/bounded-application-start-tools-20260911.xml`。最终补强后的 8 项重点测试在 PowerShell 5.1/7 下通过，见 `tmp-test-logs/bounded-application-start-final-focused-20260911.xml`。最终固定提交的完整工具和真实 Docker 验收仍待完成，不把早期版本通过结果冒充最终验收。恢复派发助手为 `tmp-test-logs/invoke-reviewed-application-recovery.py`，绑定原操作、原因、候选、应用验收、最终执行器及测试；未执行派发。

第一版真实 Docker 回归为 1 通过、1 失败（1651.96 秒）：直接启动 ID 的用例通过，旧故障复现因夹具把迁移器网络绑定至被重建的 PostgreSQL 容器 ID，先以 128 退出，未出现现场的旧结构版本错误。保留结果 `bounded-application-start-integration-20260911.xml`，不作最终验收。其 22 个测试容器已独立核验全部停止、无宿主端口，见 `bounded-application-start-initial-owned-assets.json`，存储保留。夹具改为内部桥接网络与 postgres 服务 DNS，选择未占用子网且无端口；变更独立提交 `7a672a914283b10163dff97706027d73e6ced8d6`，复审通过，升级器字节保持 b4329445…。

中间工具运行 `bounded-application-start-reviewed-tools-20260911.xml` 为 187 通过、2 失败（793.66 秒）；它启动后测试内存仍是旧夹具，缺少新增审计权限函数替身，已被作废并完整重跑，不能作为最终通过证据。最终工具和集成输出分别为 `bounded-application-start-final-tools-20260911.xml`、`bounded-application-start-final-integration-20260911.xml`，以完成后的实际结果为准。

最终工具回归已完成：**189 项全部通过，774.25 秒，无失败或跳过**。升级器与工具测试字节摘要前后保持，HEAD 7a672a9 工作树干净。真实 Docker 两项最终验收仍在运行，目标 Recover 尚未派发。

14:18 最终真实 Docker 验收完成：**2 项全部通过，1837.60 秒，无失败或跳过**，见 `tmp-test-logs/bounded-application-start-final-integration-20260911.xml`。覆盖 PowerShell 7 直接启动已验证的应用容器，以及 Windows PowerShell 5.1 复现旧 Compose 失败、注入一次临时恢复错误后由新进程完成完整 Recover。每次调用仍使用原 900 秒限制。恢复助手本地门禁通过，执行器提交 7a672a9、摘要 b4329445…、原操作和候选身份匹配；现场结果以下续记。

最终回归的 24 个相关容器已独立核验全部停止、无宿主端口，存储保留，见 `tmp-test-logs/bounded-application-start-final-owned-assets.json`。14:20:09 目标 Status 核验原操作、候选、原因、备份和待决 intent 均匹配，随后派发**一次 Recover**，证据目录 `tmp-test-logs/bounded-application-recovery-20260911`。14:21:23 的只读进度仍为原失败阶段；尚不能据此判断 Recover 结果，也未重发操作。

### 现场前向恢复与部署核验完成

14:23:13 控制端收到 Recover 成功结果：`ok=true/status=committed/error_code=""`。journal 为 `committed`、迁移阶段 `completed`、待决 Docker intent 为空；原 intent 及完整失败观察保存在 `migration.application_start_observation`。活动版本已提交为 `deploy-20260911.1` / a78c522，未重新 Apply、未恢复或降级生产数据库。

14:23:58 独立部署核验通过，证据 `tmp-test-logs/bounded-deployed-verification-fb94d68008ba4aa5a7b03c0e206bba03.json`：五项服务镜像与签名候选一致且运行，PostgreSQL/Redis healthy、API/GW ready、Web 200；数据库准确为 0013，原角色权限恢复、备份摘要匹配、独立恢复容器停止、非发布环境字段保持、启动保护摘要匹配、维护锁释放、提交审计一条。设备/点位/实时/历史计数仍为 `0|0|0|0`。

### 串口工具绑定预检

14:24 的首次绑定在备份旧回执前被辅助脚本的启动脚本身份规则拒绝，publisher 尚未执行，原六份工具/模板和回执未变；已生成的六份备份保留于目标 `C:/Ruisheng/tools/bounded-serial-rebind-9b2486db03284a699835b99731e9649e`。只读取证 `bounded-serial-binding-inspection-37385af54ede4e6cbc3e5e02a1b86b4f.json` 和 `bounded-serial-binding-inspection-f0647d2a1a68490c8cef0d03adb203d0.json` 确认回执、父目录所有权和保护正确；辅助脚本误用了额外禁止祖先属性写入的启动入口规则，触发 Windows 的 ProgramData 祖先权限。

辅助脚本改为复用签名 runner 的 `Assert-ProtectedPath` 和 `Assert-ProtectedAncestors` 校验回执，仍核验所有权、链接、文件写入和祖先替换权限；其他工具保持启动脚本身份核验。增加只读预检和保留错误详情。14:30 目标只读预检通过，证据 `tmp-test-logs/bounded-serial-preflight-9b5a7b9b23a34abb9b93c906514f1fe5.json`，零设备 TX；原失败辅助脚本保留为 `install-bounded-serial-binding-first-attempt.ps1`。

14:31:12 绑定安装完成，证据 `tmp-test-logs/bounded-serial-rebind-925612673f5d4c9bbf5078fbee0a184d.json`；回执绑定候选 a78c522 和已部署 GW 镜像，原七份工具/模板/回执全部备份，环境和活动指针保持。publisher 返回 2 保留既有正式资格阻断，安装器根据正确回执、文件摘要和 ACL 判定工具安装成功；不将该退出码视为正式验收通过。

### 单次真机复验与收尾完成

预演通过后，仅执行一次已批准的 1 号设备读取，证据入口为 [2026-09-11 真机复验](../superpowers/specs/evidence/zero-origin-revalidation-20260911/README.md)。运行 ID `e1e5dfdb-3f45-42d1-8964-86e792ab697a`，14:32:52 至 14:32:55，FC3 零起点 36/27/36/6/36。5 次发送、0 次重试、5 帧有效响应，响应长度依次为 77/59/77/17/77 字节，时延 148.449/131.626/179.645/147.464/226.938ms。未扫描、未发送设备写功能、未扩大至 38 寄存器或 81 字节。

47 项离线收尾核验通过，重新计算原始帧 CRC、数据、配置/脚本/回执/镜像绑定、远端副本摘要、请求预算和终态；四个相邻请求与前次响应的审计间隔均为 500ms。原始与独立 runner 审计一致，诊断容器已确认移除。前后生产容器 ID、镜像、启动时间和重启次数一致，生产 GW 无串口映射。

三次整读 27/36 个位置原值相同，9 个变化。27 寄存器短读对照为 3 个稳定非零一致、15 个零值、9 个动态参考；6 寄存器短读为 1 个稳定非零一致、5 个零值。未发现稳定参考的不一致，但这不证明点位身份、倍率、单位、校准或长期稳定性，也未关闭既有非零起点问题。

14:33:59 最终目标核验通过，证据 `tmp-test-logs/bounded-deployed-verification-68782715c4e54455b66f9a4d12009380.json`：活动候选/数据库保持，五项服务运行、API/GW ready、Web 200，备份完整、角色正常、维护锁释放，设备/点位/实时/历史计数仍为 `0|0|0|0`。本次单次读取授权已使用，未自动重复、未开启生产连续采集。

### 后续事项

1. 确认其他实物设备数量、地址，并取得与当前设备/固件匹配的正式点表或独立测量参照；再开展多设备、校准和持续采集验收。现有有界诊断不构成这些结论。
2. PostgreSQL TCP 冷启动修复已实现并验证，仍须纳入后续签名候选；当前运行包不含该独立提交。
3. 目标 PowerShell 7.6.5 已存在；本轮已将桌面快捷方式切换到 `C:\Program Files\PowerShell\7\pwsh.exe`，并验证版本、退出码、Edge 窗口和容器不变。SSH 维护入口仍显式使用 Windows PowerShell 5.1，后续如需统一 SSH 运行时应单独验证输入编码、JSON、退出码和进程管理，不修改 SSH 默认 shell。

本轮实现提交仅在本地，未推送；原失败记录、备份、恢复回执和测试存储均保留。

### 桌面入口后续修复完成

用户后续打开桌面入口遇到 `unexpected_project_container`，原因是启动器误把已完成升级的留存校验容器视为重复 API。修复提交 `5568e15` 已通过 41 项工具、两项真实 Docker 回归及独立复审，17:00 受控安装至目标机；17:01 同一桌面脚本在实际 PowerShell 5.1 下返回 READY / `already_ready`。服务、所有升级容器、历史记录和数据保持，备份与维护锁核验通过。详情与证据见[桌面入口修复报告](2026-09-11-desktop-launcher-repair.md)。

### 2026-09-11 晚间日期修复发布完成

日期输入修复候选 `deploy-20260911.2` 于 20:07 受控部署，Apply 状态为 `committed`。目标复核显示五项服务镜像、数据库 head `0013_serial_polling_profile`、服务健康和数据计数均符合预期，用户/有效管理员为 1/1，设备/点位/历史仍为 `0|0|0`，锁已释放。部署后本机浏览器通过 SSH 访问目标实际 Web 和后端完成 14 页面和 10 类交互巡检，定时计划和保养计划清空日期回归通过；随后目标普通用户会话 ShellExecute 桌面快捷方式，启动器退出 0，Edge SCADA 窗口存在，容器状态不变。20:33 已将桌面快捷方式切换为 PowerShell 7.6.5，20:36 普通用户启动复验通过。证据目录 `tmp-test-logs/plan-date-deployment-20260911`，完整结果见[管理员登录交付检查](2026-09-11-admin-login-readiness.md)。本轮未重复设备读取。

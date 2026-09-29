# DEV001 离线排查与配置修正（2026-09-14）

## 当前状态：已完成部署与验收（2026-09-14 18:13）

DEV001（1号泵站、地址1）已恢复真实在线，设备型号按用户地址标签保留ADDR-001。COM3对应的适配器经稳定路径/dev/ruisheng-rs485供网关使用，38个原始寄存器值约5秒一轮。18:08:59复核is_online=true，真实last_call_at=18:08:56.748895、last_back_at=18:08:57.090724，loss_count=0；五轮均有完整38点，最新值年龄1.88秒，历史43092条，网关零重启。

18:08:04已正式提交deploy-20260914.3，source 5f544916a92399b748d2dcc492636a3d4513f9af，logical sha256:687cd6e406f0a7aa3133f0f91ade230bb96fb25be551c7c299517bcdd3d3b76c。operation b7d6c7a6-caf2-40f6-a845-2ed0b4c41c29已committed，数据库保持0013_serial_polling_profile，备份完成，API/GW ready、Web200、五项运行镜像与签名候选一致、维护锁已释放。

真实目标Web验收两次通过。最终以本机Chrome经SSH登录目标后，确认列表在线、38卡片、历史查询1000行与曲线完整绘制、WebSocket open、无页面错误；默认时间范围采用上海本地时间。此页面操作来自本机浏览器，不是远程鼠标控制目标Edge。18:10在目标普通lenovo会话ShellExecute原桌面快捷方式成功，实际PowerShell7.6.5、launcher exit0、Edge应用窗口存在，新审计already_ready指向.3；11个生产项目容器身份/启动时间/重启数保持，临时任务已删除。没有重新创建或重置账号。

本次离线修复已结束，无部署/验收脚本仍在运行。用户无需再次释放COM3或确认部署。后续业务配置仍需核对真实点表的工程单位/倍率；现有38点明确标注原始值，无猜测倍率或报警阈值，也没有用SQL强制在线。下列“待部署/正在运行/离线”等均是历史过程。

最终证据：

- tmp-test-logs/serial-small-deployment-20260914/apply-result.json
- tmp-test-logs/serial-small-deployment-20260914/post-deploy-verification.json
- tmp-test-logs/run-serial-target-acceptance.remote-77f20f9245c241ddac9dada243a8da4e.json（完整签名、实际镜像加载后隔离启动通过，测试容器均停止，卷保留）
- tmp-test-logs/inspect-serial-final-target.remote-f0ced7b0e6644100bb047b894c417c15.json
- tmp-test-logs/inspect-serial-collection-20260914.remote-18c865d411ac48e390e99307953f7729.json
- tmp-test-logs/serial-online-browser-d013a506-cc9e-48ac-8454-8ccff9cadc0e.json
- tmp-test-logs/desktop-interactive-a47f4714616b4c1bba608a74f074b541.json
- tmp-test-logs/serial-online-list-9112228f-4bd7-4d02-8f2c-5d2b0291d67b.png
- tmp-test-logs/serial-online-history-2ca67736-26d5-475f-a535-0252cfceebb1.png

112单元、8真实PostgreSQL/模拟串口生命周期、3VHCI场景、6浏览器回归通过且无失败/跳过。本次因本机共享Docker image inspect卡住改为目标受限资源/内部网络/新卷隔离验收；未重启其他项目Docker。目标旧OpenSSH需要与现有发布器相同的cmd文件重定向验签，原失败均保留，最终未跳过签名或真实性验证。

用户报告添加设备后一直离线，串口工具能够正常读取，并要求远程修改。已连接目标 `WIN-OAUCM8UQUGH`，活动版本 `deploy-20260911.2`、数据库 `0013_serial_polling_profile`。目标 API/GW ready，PostgreSQL/Redis healthy，维护锁不存在。截图右上角在线仅说明页面 WebSocket 连接正常，不证明设备通信成功。

## 历史：XCOM 截图确认通信与读取方案修正（15:25）

用户新增的 XCOM 截图显示 COM3、9600/8N1，发送 `01 03 00 00 00 26 C4 10`，接收 81 字节。人工逐字节转录并独立复算：请求 CRC `C4 10`、响应 CRC `C9 50` 均通过；从站 1、FC3、起点 0、读取 38 寄存器，响应字节数 `4C`=76，符合完整 81 字节正常帧。此前“当前 36/38 读取数量未确认”的缺口已由这份用户证据关闭，不再向用户索取相同通信参数。

15:22 远程只读复核确认适配器 `0403:6001/AI06JYFW` 此时已回到 Windows，COM3 状态 OK，usbipd 无 WSL 客户端。截图 XCOM 的“关闭串口”按钮表明截图时串口已打开；后续启用网关应先释放 XCOM 串口，再恢复同一适配器的 WSL 附加。不要直接强制抢占正在使用的端口。生产 GW 仍无串口映射和环境配置，DEV001 仍零点位、零数据、离线。

15:25:46 已备份 DEV001 完整记录，用核对活动版本、网关无串口、设备原配置/版本以及零点位的受控事务，将 `read_profile` 从 `point_groups` 改为 `zero_origin_38`，`update_flag` 10→11，同时更新 `updated_at`。端口保留 `/dev/ruisheng-rs485`。目标备份为 `C:\Ruisheng\tools\device-profile-repair-ef16e41bc4904a25b9d6475aa08b1e98\device-before.json`，SHA256 `fade5bc9f31696a492d569aaaeddc9495512ec4db010d33a69b6ead1f620fe3b`。GW 身份、启动时间和重启计数保持。此修正没有设备通信，没有导入点位、重新部署或改写在线状态；尚不等于完成采集上线。

现有《修正后485点位表.docx》与截图有具体差异：

- 地址 16..18 为 `FC22/FC22/FC18`；即使按有符号解释再采用表内 `/10`，也得到 -99/-99/-100，超出表内 -1..1 的功率因数范围。不能直接采用该倍率，也不能把猜测的 `/1000` 当成已确认。
- 地址 0 为 `0003`；表中每字节只描述 0/1，无法解释低字节 3。位掩码编码有可能，但尚未确认。
- 地址 25 为 5000；文档“5000→50.00Hz”的例子要求 `/100`，与正文“值=DATA”不一致。
- 文档附录提及 CRC 高字节在前；本次实际回包符合标准低字节在前。

已保存通信证据及串口环境草稿，并询问当前设备与点表的对应关系、设备型号/固件。通信参数已经明确；剩余缺口是点位编码/倍率核对、网关串口接入以及启动/维护入口一致性。温湿度等按文档可计算的值仅是候选解释，没有作为真实工程量写入生产库。

证据：

- `tmp-test-logs/xcom-confirmation-20260914/xcom-success.png`（SHA256 `b0f944aa87c66fb6edd730e699359c88ce8dcc95917cfbbbc967b6471e171050`）
- `tmp-test-logs/xcom-confirmation-20260914/confirmed-communication.json`
- `tmp-test-logs/xcom-confirmation-20260914/analyze_screenshot.py`（离线复算，无硬件 I/O）
- `tmp-test-logs/xcom-confirmation-20260914/site-serial.env.draft`（本地未启用草稿）
- `tmp-test-logs/inspect-device-offline-20260914.remote-02e405b4339747b3a1c7e77996fb28f5.json`
- `tmp-test-logs/repair-device-profile-20260914.remote-f3b255a8b5ce4305aa17d6e20b47a628.json`
- `tmp-test-logs/inspect-device-offline-20260914.remote-edc46d3ac6264eba98b81cd7dde398cb.json`：15:26:26 独立复核读取方案和版本 11 已持久化，五项服务健康状态正常，仍零点位/数据、设备离线、GW 无串口配置，COM3 仍在 Windows。

## 直接原因

14:39 数据库中只有一台有效设备 `DEV001`（1号泵站）：串口模式、端口 `COM3`、从站 1、9600、5秒间隔、`point_groups`、启用。`last_call_at` 和 `last_back_at` 均为空，点位、实时记录和历史记录均为零。网关容器没有 `GW_SERIAL_PORTS` 环境变量，也没有 `HostConfig.Devices` 映射。

14:42 通过读取 WSL sysfs 核验适配器 `0403:6001 / AI06JYFW`，稳定路径 `/dev/ruisheng-rs485` 指向 `/dev/ttyUSB0`，是有效字符设备。`Ruisheng-Serial-Hardware-Attach` 正在运行，状态为 ready；此时 Windows 没有枚举出 COM 端口，适配器已经交给 WSL。Windows 串口名 `COM3` 不能用于这个 Linux 网关容器。

目标硬件配置仍为原来的仅附加适配器模式：设备型号、线路参数和点表引用为 UNRESOLVED，尚未配置持续采集。已安装的轮询实现只在网关配置端口且设备有合法点位时发送请求；当前两个条件均未满足。这解释了设备离线且没有请求时间的现象。原资料对读取 36/38 个寄存器、点位语义和倍率仍有未决，不能从“1号泵站”名称推导型号或强行导入旧点表。

现场有两个 XCOM V2.0 进程；未关闭工具、未读取进程内存、未接管或打开串口。其程序目录未发现可供核对的 INI/JSON/XML 配置文件，已向用户询问成功读取的参数和收发报文。

## 已完成的修正

14:46 在原始设备记录备份后，将 `DEV001.serial_port` 从 `COM3` 修正为已核验的 `/dev/ruisheng-rs485`，`update_flag` 从 9 增至 10，供网关周期配置刷新发现。只更新串口路径、版本计数和更新时间。数据库事务核对了设备身份、原配置、版本及零点位，并使用表锁避免与点位创建竞争；不符合原观察时会回滚。

备份位于目标 `C:\Ruisheng\tools\device-port-repair-4101525096f7446d897b883010a33961\device-before.json`，SHA256 为 `e169beaaf6fc9794ecabadfaa70f0fc39de3d2339927a86227ee297976bdff6f`。同目录 `repair-result.json` 记录实际结果。网关容器 ID、启动时间和重启次数保持，未发出物理设备命令，未重启服务。

14:48 独立只读复核确认新路径及版本 10 已持久化，五项服务、活动版本、维护锁状态均正常。仍为零点位、零采集记录且离线；此结果符合尚未配置采集的状态，未宣称修复上线。

本次修正尚不等于上线。待取得当前成功的串口参数和读取地址后，应补齐外置站点串口配置、明确采集点位，验证 Compose 仅向 GW 映射核验的适配器并配置对应端口。所有启动/维护入口需使用一致的外置串口 override，保留签名候选；不得只手改运行容器造成下次启动的配置漂移。然后受控启用并核对实际收包时间、在线状态和点位数值。不能通过直接设置 `is_online=true` 代替通信验收。

## 证据

### 15:06–15:07 目标机在线后的复核

用户告知目标机已在线后，重新通过 SSH 连接并核对主机身份。15:06:56 活动版本仍为 `deploy-20260911.2`，五项服务运行、API/GW ready，维护锁不存在。DEV001 仍使用已修正的 `/dev/ruisheng-rs485`、版本计数 10；`is_online=false`，发送/回包时间均为空，点位、实时值及历史仍为零。GW 仍未配置 `GW_SERIAL_PORTS` 或串口设备映射，容器没有重建或重启。

15:07:46 再次核验同一 USB 适配器及稳定路径，附加任务 ready。现场硬件配置的设备信息和通信参数仍未填写；两个 XCOM 进程仍存在。电脑远程连接恢复没有改变采集配置，也没有证明下位机上线。

本轮仅只读复核，没有重新部署或打开物理串口。启动/维护入口仍需统一接入外置串口配置；后续配置和实际读数验收仍待当前串口工具成功读取的参数、请求/响应及对应点位说明。

- `tmp-test-logs/inspect-device-offline-20260914.remote-34bb199bdc344cd08e79cda7beaa5d30.json`
- `tmp-test-logs/inspect-device-serial-path-20260914.remote-4005f06ac9e3445a8eee1959cbee6e6e.json`

### 首次排查与修正

- `tmp-test-logs/inspect-device-offline-20260914.remote-76502db12b8a4393b1a28fb656f1348e.json`
- `tmp-test-logs/inspect-device-serial-path-20260914.remote-9c2551a6bbdd476b8c15bf9c2f791f01.json`
- `tmp-test-logs/inspect-xcom-config-location-20260914.remote-04c4fb69fab84d1296beaea1904943e4.json`
- `tmp-test-logs/repair-device-port-20260914.remote-0cfa482ff1bc48ac8a2898ad7fbe6550.json`
- `tmp-test-logs/inspect-device-offline-20260914.remote-7cc92ef40fde4e20ba441e9d989d8f42.json`

## 2026-09-14 16:25 接续：真实采集已恢复，在线状态缺陷待修复

用户确认型号为地址标签、XCOM 已关闭释放 COM3；已按当前请求启用真实采集。外置 site-serial.override.json 接入桌面/维护/升级入口，普通用户启动器 hash c9a27920b676d82d6ca1a6d1dd786bcc03bab25288f44e4f9cd1d22aba6a7cb4，快捷方式仍 PowerShell 7。DEV001 使用 ADDR-001、9600/8N1/slave1/FC3/0/38、5秒轮询，38个明确标注原始值的点位，版本12。不推测文档中存在矛盾的工程倍率。

旧 USB VHCI slot0/ttyUSB0 遗留导致网关 open 阻塞；已按实际 bus4-1/devid00040001 选中 ttyUSB1，修补 serial_hardware_attach.ps1 的活跃 VHCI 选择、审计目录 ACL 和 PS5.1 stdin BOM。仅重建 GW，未重启主机/WSL/Docker。当前 GW 93b7af7843334e5dd5bd765e7c74d1ff1394712639725cb4c3b7cd687eed0ca5；38实时值、1292历史值、最近5轮各38值，最新数据年龄1.97秒，restart0。主证据 inspect-serial-collection-20260914.remote-2ed65bb95ced4a9196a9e7852f562f37.json。

应用仍显示离线：网关从未持久化 devices.is_online/last_call_at/last_back_at。不能 SQL 强制 true；继续实现由真实有效回包驱动的状态及过期判定，测试后签名发布。当前候选仍 deploy-20260911.2/221f81c4，串口修复独立树 C:/ProgramData/Ruisheng/publisher-build/serial-online-20260914，基于8dfa124，改动尚未提交。下一新候选必须保留221f81c4日期修复。根工作树其他dirty保留。禁止重跑已有写死旧版本/旧hash的Apply/repair脚本；当前无维护锁。浏览器登录成功，在线断言失败；尚未桌面最终验收。

## 2026-09-14 16:56 发布准备接续

状态修复已实现并提交2869735e8d048044d3cd36597d02b576405c4568（隔离树serial-online-20260914），112单元/8真实PG生命周期/3活跃VHCI shell测试通过。运行状态由实际发送和校验成功回包驱动；3次连续失败或max(10秒,3轮询周期)过期离线，API独立过期，tenant/version/endpoint/disabled/deleted保护，配置变更清空在线状态。未迁移数据库。根文件均在校验原内容匹配后同步并备份serial-status-root-before-2869735。

附带发现历史页UTC默认时间被当本地解析，偏差8小时。新树 C:/ProgramData/Ruisheng/publisher-build/serial-history-20260914，提交36f8471273398e7588b929f8f5b005f1d3d925ab修复本地时间及秒精度/空日期。两时区+四既有计划日期浏览器6项通过，类型检查通过。Web镜像b916bba16919826875cd4b150660d7ea6d1e2ef01a24d5e99782d52c130d397b，API镜像bba9a8077ffebb365412ce3efd04d6451fef0d4078d0f043d33daed9aa43e25e，GW镜像89bec78660bf46a2a2af9f25231fe4d9b65c0d65b8f4ddf8cbc411521a5c313b。新树代码干净（node_modules junction忽略；test-results已移到根tmp-test-logs证据）。根历史文件已备份同步。

中间候选deploy-20260914.1已签名验证，logical sha256:3238879e230c5bc52c4346b83114efaeaa9136278187d7e99230644d2e1bd2aa，仅作后端镜像复用源，**没有部署**。最终候选deploy-20260914.2正在build-serial-final-candidate.ps1，exec11739，必须先等完成，不重发构建。最终绑定输出serial-status-final-build-result.json。目标仍deploy-20260911.2。

下一步：verify-plan-date-candidate-apps.py --source 新树 --candidate最终候选 --trust publisher-trust --evidence-root tmp-test-logs运行隔离整包验收；invoke-serial-status-deployment.py Plan/Apply（包装器已更新最终commit/id且验证XML），Apply前可用prepare-serial-final-delta.py生成delta，再seed-serial-candidate-delta.ps1预填入Plan operation的protected incoming目录。该种子方法仅重组新候选归档，不改旧签名候选/运行容器，校验old/delta/newSHA。中间候选实测postgres/redis/web可复用338MB，最终web变动后可能需全传28MB；API116MB/GW86MB需全传。正常Apply仍会完整验签，不跳门禁，ResumeUpload跳过已校验重组文件。

浏览器serial-online-browser.cjs已增强为上海时区，登录后在线行、38卡片、点35历史有行、列表复验及3截图。run-serial-online-browser.ps1用既有DPAPI密码stdin，不打印凭据。launch-target-serial-online-20260914.ps1待运行；升级后可能新增退出的migrator容器，先只读确认实际数量再调整固定11的快照断言，保持前后身份一致保护。当前无Plan/Apply/种子传输进行，无维护锁，无subagents。

## 2026-09-14 17:04 正在部署（不要重复派发）

最终候选 deploy-20260914.2 已完成签名校验：source36f8471273398e7588b929f8f5b005f1d3d925ab，logical sha256:16fe5149926ee092b08336be2c6567a391c78df0b1addf889438381fa5dddd70。整包隔离验收全部通过，证据 plan-date-apps-db42002a0f4f45d591539b3b4cc3c5fc/result.json，隔离容器已停止。Plan通过，same_head/0013，operation b397221d-b228-4aab-8f22-9bbe25b6fecf。seed-rebuild.remote-4ec6920517bf4ab2b2f0f668ff719116.json验证目标incoming重组postgres/redis归档SHA与最终签名包一致，复用310509568 bytes，未改旧包/运行服务。

17:03:57 已派发一次 Apply --ResumeUpload --Approved，执行会话71436进行中，绑定/日志目录tmp-test-logs/serial-status-deployment-20260914。不能重发Apply或盲目Recover。只读进度脚本inspect-serial-status-progress.remote.ps1已绑定当前operation/finalcandidate，可通过invoke-target-desktop-script.ps1运行。总包542585483 bytes，其中310721770已重组；剩余api116453122/gw86263065/web28557896+少量工具需上传。目标此刻仍旧版本采集，等待真正committed后验收。

最终候选构建11739、Plan1330、种子19912、验收66591等均已结束；只有Apply71436在运行。已准备inspect-serial-final-target.remote.ps1（健康/容器/锁/活动版本）和inspect-serial-collection-20260914.remote.ps1（38实时/历史/last_call/back/online）。部署后再run-serial-online-browser.ps1（保密凭据），launch-target-serial-online-20260914.ps1（可能需要核对部署后实际容器数再更新固定11断言）。

## 17:27 重要：大包上传已暂停，改用精简签名候选

17:23:36 验证本机controller15352及唯一sftp17904为Apply上传阶段后，停止这两个本任务进程；Python包装器已正常记录退出，71436已结束。所有目标incoming文件保留，未进入目标Apply/journal阶段。证据serial-status-deployment-20260914/upload-paused.json。不要重发14.2 Apply或Recover。

临时香港中继已成功恢复自动：restore-target-auto-route-20260914.remote-8795aa8088af4927a21aa32af592646a.json，目标记录restored_at=17:24:22；此前两次exit255未执行，最后一次暂停上传后成功。无临时网络改动待恢复。

新隔离树serial-small-update-20260914，commit5f544916a92399b748d2dcc492636a3d4513f9af。应用源码与36f8471完全相同，仅新增deploy/serial-patch-{api,gw}.Dockerfile和packaging说明。新API/GW以已认证deploy-20260911.2的原镜像为底，COPY完整当前src/shared，依赖/锁/迁移/scripts不变、无删除源码，基底ID已验证；Web继续使用14.2已验证的b916bba...。新API image9a2ebe8af59e0dbbbd4892d93669957474def3ec38cf25bcc0e4169e685d3794，GW43ee186a7b543c5e72be6b999b7442a77ce6a765d7fe6c1ccca362e731db74f7。两个image build均成功。

正在运行build-serial-small-candidate.ps1创建deploy-20260914.3（新会话见当前工具结果）。该builder使用serial-prefix-archives.py保存旧tar blob原始前缀，追加新blob/顶层索引，保留惰性未引用旧blob；无重复成员/链接，完整候选签名/镜像身份照常验证。导出原始镜像保留serial-small-image-exports，最终hash输出serial-status-small-build-result.json。完成后务必对14.3再次运行verify-plan-date-candidate-apps.py（代码无需重测，整包真实启动/加载要验收），prepare delta基准仍目标deploy-20260911.2；预计只传少量新source/web层。不能声称14.3已发布/已安装，尚待构建、测试和Plan/Apply。

14.2候选和未完成上传目录保持，用于审计；未来14.3必须新operation与新证据目录，不能复用b397...旧绑定。root已同步新增3文件，其余本次代码之前均已备份同步。

## 18:00 目标隔离验收接续

本机共享Docker镜像inspect故障后，将同一整包验收工具移至目标受保护staging，生产容器保持。五个镜像真实load已经通过。首轮目标验收plan-date-apps-dffe8da9b4dc405da72de6ae0d940652停在系统OpenSSH的管道stdin验签，未创建容器；精确停止自己的ssh-keygen PID7000后，原验收正常收尾为失败，production_unchanged=true。失败证据run-serial-target-acceptance.remote-6222499d97bf4f5581e1a826b0953af0.json。

第二轮采用现有发布器同等文件输入验签方式，仍调用固定系统ssh-keygen校验完全相同的signed bytes/public trust，无跳过验签。新工具verify-plan-date-candidate-apps-fileinput.py SHA256 ae450ad051943576e82365356133c306de5dc13c610a237f15b6b831203e3a44。session63791正在运行run-serial-target-acceptance.remote.ps1，勿重复执行。inspect-serial-acceptance-progress.remote.ps1可查看progress、结果及受测容器。收到passed后，使用该本地远端结果JSON给invoke-serial-small-deployment.py Apply --acceptance；Apply尚未派发。

## 18:05 整包验收通过，正式Apply已派发

最终目标隔离验收plan-date-apps-3720898e6d594775bf483fd472117b89全部通过：签名、5镜像身份、DB0013、API/GW ready、Web资源200/管理ACL403、内部网络且无主机端口/串口、所有测试容器停止、生产容器和活动版本保持。证据run-serial-target-acceptance.remote-77f20f9245c241ddac9dada243a8da4e.json。验签最终使用与现有verify-candidate.ps1一致的cmd文件重定向；直接Python文件stdin仍超时的第二轮失败也已保留、没有创建容器。最终验收脚本cmdinput哈希ffde7b88524ff8111eecfe94894e90a0445ac91c6e3333af81b3d744db8e2e4f。

18:05:20已执行一次deploy-20260914.3 Apply，operation b7d6c7a6-caf2-40f6-a845-2ed0b4c41c29，session28790进行中。所有前置测试/候选/源码/目标实加载/隔离启动证据在派发前核对通过。禁止重复Apply或盲目Recover。完成后检查apply-result，远端只读验收、真实浏览器、普通用户桌面启动。18:04目标Console1仍解锁，快捷方式仍PowerShell7，证据desktop-session-6d7768b250e748ada6b85d72f0e90ce2.json。

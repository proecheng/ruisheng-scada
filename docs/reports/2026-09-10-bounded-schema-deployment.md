# 多设备有界数据库升级：候选与部署验收

本报告继续既有授权流程，所有时间为北京时间。发布源码保持干净；后续记录只写根工作区及独立证据目录。不得将模拟设备或诊断读取称为正式实物验收。

## 已完成的发布准备

- R9最终工具181项、双PowerShell真实Recover/完整Apply四项通过；独立审查通过，四项defer保留于R9报告。
- 126个baseline范围文件提交检查通过，实际限定本地提交113文件，提交时原钩子再次通过；未推送。
- 本地提交：`0a7bbf35f6357491312635b93d0238d539e21e1f`。
- 签名候选：`C:\ProgramData\Ruisheng\publisher-output\deploy-20260910.1`。
- 候选逻辑身份：`sha256:b2b806750876057a1c9249eeb7c0cdc2baac63b9720774bec0b35d0555d2f0e4`。
- MANIFEST SHA256：`a71c3fba0f0df00838f531b0b796079602d1edcfa620df20dd9da1891798e862`。
- 批准迁移0013的SHA256保持`df20fff11e1b8ea04ef4ce69126c29021e221685ad27a138bbfafe10143ac5d1`。

## 隔离应用验收：原失败保留

首轮`tmp-test-logs/bounded-apps-72a4cc5bfe084beb804dba034242b357`签名验证成功，未启动应用容器。助手遗漏ReleaseArtifactError的具体原因，且stage仍显示verify_signature，不能据此称验签失败。只读复现确认五镜像身份吻合；清理后的环境只有SystemRoot和测试变量，缺少ProgramFiles，Docker无法发现系统Compose插件，config命令报unknown flag: --env-file。环境差分验证确认仅补ProgramFiles即可正常发现Compose v5.1.1。仅修正独立验收助手，补阶段与脱敏错误取证；候选和源码未改。

第二轮`tmp-test-logs/bounded-apps-a12639e035ad4a3a9ee49f31cafeafcb`通过签名、五服务镜像身份、真实API/GW外部及内部健康、0013数据库头和空设备/点位/历史检查，随后网页端口断言失败。全部本轮应用容器已停止，存储保留。失败原因是Docker internal网络实际不发布宿主端口；独立惰性容器复现确认请求127.0.0.1映射而运行时80/tcp映射为空，见`tmp-test-logs/bounded-port-diagnosis-bfa11dde14b84f2cbe6315e654bcfad2/result.json`；诊断容器已停止并保留。

验收助手取消宿主端口请求，验证真实网络internal及五服务无宿主端口、无设备映射、仅加入本轮网络；通过同网络API容器发送HTTP请求验证真实nginx登录页面、JS/CSS状态/类型/内容摘要和管理路径403。不更改候选、不替换应用健康实现。

第三轮`tmp-test-logs/bounded-apps-64147a06373c45bca7c2dcbf1cbc907a`在全新数据库首次初始化时失败：11:24:15Z启动迁移容器，11:24:19Z因TCP ConnectionRefused退出；PostgreSQL完成initdb脚本后到11:24:25Z才开始监听TCP。候选Compose的pg_isready未指定-h，临时Unix socket服务器也会使健康检查成功。原baseline同一行已存在，属于既有首次安装竞态，不是0012→0013迁移语义变化。脱敏完整日志与状态见该目录failure-diagnostics.json；全部本轮容器停止并保留。不能称首次安装一条compose命令已通过，也不能称该生产缺陷已修复。

第四轮保留候选全部应用/迁移/健康代码，验收编排先启动基础服务并确认最终TCP监听，再启动应用。这符合本次已有数据库升级后的真实应用验收范围；不证明未经修复的首次安装启动顺序可靠。首次安装问题另列未决，后续应将正式Compose PostgreSQL健康检查改为最终TCP监听，并用真实空卷冷启动验证。本次发布源码继续保持批准范围和签名身份。

## 目标机预检

19:19:16，目标`WIN-OAUCM8UQUGH`仍运行`deploy-20260907.1`、数据库0012，五服务运行、API/GW ready、Web200，设备/点位/实时/历史四表为`0|0|0|0`。新guard和维护marker均不存在，changes=false。证据：`tmp-test-logs/bounded-target-health-6b455d2ba5024193ab6e4d0766bf9611.json`。

## 待完成

第四轮真实应用验收已经通过，证据`tmp-test-logs/bounded-apps-43f74ecf86264ccc9f2af03cf4417249/result.json`。签名与五镜像身份吻合，0013、API/GW外部和内部健康通过，真实nginx登录页面200，三项入口JS/CSS资源状态、MIME、内容摘要均通过，管理路径403。本轮网络internal、无宿主端口、无USB映射，全部容器已停止、存储保留。startup_mode明确为infrastructure_tcp_ready_then_applications，保留首次安装启动竞态的限制。

接下来依次执行目标启动保护安装与入口核验、同一操作ID的Plan/Apply、备份还原与部署后验收、串口工具绑定及B11既有范围的一次有界读取。此条记录时尚无目标写入、生产迁移或Modbus发送。

## 目标启动保护安装

19:29:30再次确认目标旧版/0012与五服务健康，四表仍0，证据`bounded-target-health-4a5cc3521acb419a9431cf2151d8f785.json`。随后开始安装；首次调用只完成受保护暂存及Docker辅助脚本原件/摘要/ACL备份、辅助脚本ACL收紧，SSH返回exit1，原启动器未替换，guard回执未出现。只读状态证据为`bounded-guard-diagnosis-7e8d8fad93fc4551a7533fa6ea35cdc2.json`及`bounded-guard-diagnosis-2f99ced0f9dd49d39ace89cf14b340a1.json`。

目标有效脚本策略为Restricted。19:33:59对同一暂存目录中的精确已审核文件、原备份、既有启动器及当前版本再次核验后，用单次PowerShell进程的ExecutionPolicy参数完成安装；全局策略前后均为Restricted。正式候选及发布源码未修改。安装成功，桌面图标与受保护启动器就位，启动器SHA256为`e72202e31595e1c9fe6143632d39befca0986414f4089a9d72d084103827057a`，原文件和备份保留；未改服务、计划任务或USB。原始首次SSH stderr未保存，不能唯一证明其错误文本；读后状态与同文件单次进程安装通过支持策略阻止执行的诊断。

安装证据：`tmp-test-logs/bounded-guard-resume-6657c924fd164a4b9ac75dd9cd34ffbf.json`；目标原件及回执目录：`C:\Ruisheng\tools\bounded-guard-install-af32cde116df4e39b49c5ded7ffa66d4`。实际入口核验正在执行，生产迁移尚未开始。

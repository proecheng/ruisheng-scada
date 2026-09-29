---
title: '升级后桌面入口识别已结束的升级留存容器'
type: 'bugfix'
created: '2026-09-11'
status: 'done'
baseline_commit: '7a672a914283b10163dff97706027d73e6ced8d6'
context: []
---

<frozen-after-approval reason="用户已要求修复目标机桌面启动错误；必要实现、验证和受控安装在授权范围内">

## Intent

**Problem:** 目标活动版本 deploy-20260911.1 已正常运行，但双击桌面入口报 unexpected_project_container。升级留下四个已退出的迁移校验容器，它们继承 api/project 标签；已完成的新迁移器与旧 migrate 也同时存在。启动器按服务名唯一计数，把留存证据误作重复服务。

**Approach:** 在闭集服务检查中识别经受保护成功升级记录和实际容器状态核验的留存辅助容器。它们不占用日常服务名额，仍保留全部证据。完成桌面实际入口验收。

## Boundaries & Constraints

**Always:** 核验留存容器完整 ID、名称、操作标签、实际镜像、成功结束且 PID 为零、restart=no、无宿主端口及无危险设备/挂载；关联本受保护站点的已提交且阶段 completed 的 bounded_0012_0013 journal。校验容器须 network=none、只读根文件系统、无挂载；实际新迁移容器须匹配 journal 中记录的容器 ID 和名称。保留原有维护锁、漂移、镜像、回环端口及未知容器拒绝逻辑。已运行状态重复启动应到达 already_ready。

**Ask First:** 超出桌面入口修复的功能或硬件范围。

**Never:** 删除、改名或重标记留存容器；更改历史 journal/审计来迎合检查；绕过未知或活动辅助容器；重放 Apply/Recover；更改签名候选、数据库、串口或连续采集配置；修改 SSH 默认 shell；推送提交。

## I/O & Edge-Case Matrix

| 场景 | 输入 | 预期行为 | 错误处理 |
| --- | --- | --- | --- |
| 已完成升级 | 五个正确服务、旧 migrate、成功的新 migrate 和隔离校验容器 | 留存辅助容器核验后不计入服务唯一性 | 到达健康检查和 already_ready |
| 伪造辅助身份 | 相似名称但操作、镜像或记录 ID 不匹配 | 不接受例外 | 原错误关闭 |
| 尚未完成 | 辅助容器运行/重启/退出失败，或 journal 未提交/损坏/缺失 | 不接受例外 | 原错误关闭 |
| 危险配置 | 校验容器有网络、写挂载、特权或设备 | 不接受例外 | 原错误关闭 |
| 普通重复服务 | 额外 API 或第二个普通 migrate | 继续拒绝 | 原错误关闭 |

</frozen-after-approval>

## Code Map

- `tools/start_ruisheng_local.ps1`：项目容器闭集检查、活动发布、维护保护与幂等入口。
- `tools/install_ruisheng_desktop_launcher.ps1`：仅更新已审查启动器的 LF 规范化固定 SHA-256，不改变安装或保护回执行为。
- `tools/remote_full_upgrade/target-updater.ps1`：只作为既有辅助容器和 journal 契约的依据，不改变其实现。
- `tests/tools/test_desktop_launcher.py`：PowerShell 5.1/7 的实际函数与拒绝分支回归。
- `tests/integration/test_desktop_launcher_retained_start.py`：两引擎实际 Docker 启动与数据库版本门禁，使用独立测试项目、无网络或宿主端口。
- `docs/REMOTE_DEBUG.md`：现场入口和故障解释。

## Tasks & Acceptance

**Execution:**
- [x] `tools/start_ruisheng_local.ps1`：增加受保护已完成升级记录读取和留存容器核验，保留普通服务唯一性与有限超时。
- [x] `tools/start_ruisheng_local.ps1`：存在合格留存证据且既有服务停止时，完整核验五个服务的 ID、名称、标签、镜像及状态；按 ID 启动 PG/Redis，等待依赖健康并只读确认数据库版本与活动候选一致，再启动 GW/API/Web。共用截止时间，启动前重验租约、配置、指针和容器身份；该分支不执行 Compose 或迁移。
- [x] `tools/start_ruisheng_local.ps1`：从活动 Compose 与固定镜像默认值构造配置期望，核验实际容器的环境、命令、入口、用户、目录、卷/绑定来源与读写权限、网络和设备权限；五服务全部通过后才允许任何启动，不输出环境秘密。
- [x] `tests/tools/test_desktop_launcher.py`：复现现场重复 api/migrate，覆盖异常身份、状态、记录、权限和配置；在两个 PowerShell 引擎下运行。
- [x] `docs/REMOTE_DEBUG.md`：记录正常升级留存容器的识别及未知容器仍被拒绝。
- [x] `tools/install_ruisheng_desktop_launcher.ps1`：固定新的启动器摘要，验证安装回执、保护权限和旧文件备份回归。
- [x] 现场受控安装：保存旧启动器、保护回执与快捷方式，安装固定摘要修复，保留历史升级记录；执行桌面同一脚本的无 UI 验收，并核验快捷方式目标、参数和工作目录。

**Acceptance Criteria:**
- Given 已完成升级且服务正常，when 运行安装后的桌面启动器，then 输出当前候选 READY、记录 already_ready，五个服务 ID/启动时间/重启次数保持。
- Given 任何未知容器或不合格留存容器，when 检查项目容器，then 在任何 Compose 变更前拒绝。
- Given 合格留存证据和完整的五个正确既有服务，when 全部或部分服务停止后运行入口，then 仅按 ID 启动停止服务，依赖健康和数据库版本匹配后再启动应用，所有辅助容器保持；缺失服务、身份或版本不符时关闭启动路径。
- Given 同名同镜像服务使用错误的数据卷、命令、环境或网络，when 请求按 ID 启动，then 在第一项服务启动前拒绝，即使错误数据库也声称处于 0013。
- Given 已完成受控安装，when 核验保护回执和桌面快捷方式，then 新脚本摘要匹配、访问权限正确、旧文件备份保留，历史 journal 和签名候选摘要保持。

## Design Notes

辅助容器的名称或标签单独不足以获得豁免；需要已提交的受保护 journal 和完整 Docker inspect 交叉核验。普通 migrate 仍保持既有唯一、已退出规则。无需清理升级留存来修复启动；不改变当前运行包内容。现场证据：`tmp-test-logs/desktop-launcher-failure-9a28452972254accbe9b3cdcf99ed65e.json`。

日常恢复遇到合格留存证据时，原 Compose 收敛可能重新处理继承 `api` 标签的校验容器或旧 migrate，因此改用同一完整五服务集合的固定 ID 启动路径。缺少服务或安全核验失败时拒绝；数据库版本不符时允许已经启动的 PG/Redis 保持原状，但不会启动应用，也不会猜测迁移、回退或重建。

## Verification

- 最终工具回归：41 项全部通过，无失败/跳过，包含 PS5.1/7 分支；`desktop-launcher-final-tools-20260911.xml`。
- 两引擎 AST、Ruff 和提交检查通过；三路审查及配置/挂载子目录补强复审均已关闭。安装源码提交 `5568e15a7c017a1603a8043fef034cbabb34cd8c`。
- 最终真实 Docker 回归两项通过，429.71秒，源码摘要前后保持；证据 `desktop-launcher-final-docker-20260911.xml`。验证全停、部分运行、全部运行幂等和错误数据库版本拒绝。此集成使用真实 inspect/start/SQL，替换机器权限/租约及应用健康外围；相关真实权限和健康流程由工具测试与目标预检补足。
- 17:00 受控安装完成；17:01 目标同一桌面脚本在实际 PS5.1 执行 `-NoBrowser -NoUi` 返回 READY / `already_ready`，退出码0。11个项目容器身份/状态保持、Web200、数据计数0|0|0|0、历史记录与备份保持、维护锁释放。证据 `desktop-launcher-acceptance-9aba93dafdb847a1b2e28ebc97c7bda3.json`。

## Spec Change Log

- 2026-09-11：实现前补全停止服务场景。仅识别留存容器会令后续 Compose 再启动旧迁移器，或替换升级证据；本次增加既有服务按 ID 启动及数据库版本门禁。保留原健康快速路径、双租约、未知容器拒绝、留存证据及受保护升级记录核验。现场仅验收已运行入口，停止服务场景在本地隔离环境验证。
- 2026-09-11 独立审查：补全实际容器配置绑定，避免同名镜像使用另一个同版本数据库卷；保留已验证的闭集、固定 ID、顺序与版本门禁。集成夹具改为唯一项目标签，避免测试残留污染生产项目枚举；首次测试结果保留，修正后重新执行。

## Suggested Review Order

- 从桌面健康分支进入既有服务启动路径。
  [start_ruisheng_local.ps1:1571](../../../tools/start_ruisheng_local.ps1#L1571)

- 交叉核验同站点成功升级与实际留存容器。
  [start_ruisheng_local.ps1:708](../../../tools/start_ruisheng_local.ps1#L708)

- 校验镜像默认配置、活动配置和真实数据来源。
  [start_ruisheng_local.ps1:1290](../../../tools/start_ruisheng_local.ps1#L1290)

- 固定容器身份，依赖和数据库版本通过后启动应用。
  [start_ruisheng_local.ps1:1466](../../../tools/start_ruisheng_local.ps1#L1466)

- 安装器仅接受已审查的启动器摘要。
  [install_ruisheng_desktop_launcher.ps1:94](../../../tools/install_ruisheng_desktop_launcher.ps1#L94)

- 覆盖异常配置与受控启动顺序。
  [test_desktop_launcher.py:557](../../../tests/tools/test_desktop_launcher.py#L557)

- 使用隔离资产验证实际Docker启动和SQL门禁。
  [test_desktop_launcher_retained_start.py:1](../../../tests/integration/test_desktop_launcher_retained_start.py#L1)

# R7 独立验收审计

审计角色：bmad-quick-dev step-04 Acceptance auditor。仅根据所给冻结差异、规格及发布树独立检查；未读取其他审查员的发现正文。

## 结果

发现 1 项有具体可达调用链的静态结构语义证明缺口，见 A1。缺失的目录字段及其他门禁不能补足该字段均由源码确认；所述精确 Timescale 镜像中的 ICU 可用性、ALTER 后实际 deparser 输出及完整 Recover 放行尚未运行验证，应由主线程在独有隔离资产中做最小复现后最终分类。不能把这份报告称为运行时复现通过，也不能据此声称目标已经受影响。

R7 本次新增的启动器/回执祖先保护块没有发现额外的已确认验收偏离。候选提交、签名构建、真实 API/GW/Web 隔离验收、目标入口安装及受控升级尚未完成；规格清楚地把它们列为待执行发布步骤，本审查不将这些待办误列为代码缺陷，也不将本地测试声明视为部署通过。

## A1 — [P2] 当前 0013 结构证明未绑定 read_profile 的隐式 collation

文件：`tools/remote_full_upgrade/target-updater.ps1`，主要位置 2209–2213、2231–2257；可达恢复链 3465–3488、2984–3007。

违反的要求：规格验收条件“结构仍含原关键词但语义已变……校验0012/0013……拒绝”；context“精确入口和结构”要求完整 CHECK 语义，并明确额外允许读取方案必须拒绝。此处属于所给 baseline 差异新增的结构证明代码，不是 R7 祖先补丁本身引入的问题。

具体缺口：

- `columns` 只投影 name、type、required、default、generated、identity，没有 `pg_attribute.attcollation`，也没有其稳定 collation 身份及行为属性；比较循环同样不检查它。
- `checks` 只比较 `pg_get_expr(conbin, conrelid, false)` 文本和 convalidated。隐式列 collation 属于 Var/比较运算符的目录语义，不需要在原 CHECK 表达式里出现显式 `COLLATE`，`SET search_path=pg_catalog` 也不会把它改为字节敏感比较。
- 2224–2225 的 `column_collations` 仅证明唯一索引键与各自当前列的 collation 一致。该索引只有 serial_port、modbus_addr；read_profile 不在索引里，因此该谓词不能证明它的比较语义。

可达状态是已经完成批准迁移的 **0013**，不是尚无 read_profile 的 0012。若故障恢复前该列被改为一个大小写不敏感的 nondeterministic ICU collation，`varchar(20)`、默认值、NOT NULL、CHECK 名称与可见表达式、地址唯一索引以及 Alembic head 均可保持；但第一项 CHECK 会额外允许 `POINT_GROUPS` 等值。以下仅是未执行的最小隔离复现候选，不是本审查执行记录：

```sql
CREATE COLLATION public.rs_casefold
  (provider = icu, locale = 'und-u-ks-level2', deterministic = false);
ALTER TABLE public.devices
  ALTER COLUMN read_profile TYPE varchar(20) COLLATE public.rs_casefold;
```

应在同一隔离事务中比较前后完整 guard 查询的 JSON、实际 CHECK 对大小写变体的行为，再调用未替换的真实 `Assert-BoundedSchema`，避免只用模拟 JSON 证明问题。源码层面并未存在能据此字段拒绝的比较。

其他已实现证明不能覆盖此缺口：

1. R6 的数据库属性证明读取 `pg_database.datcollate/datctype/datlocprovider/...`（2484、2513–2571），改变单个列的 collation 不改变数据库默认属性。
2. 源数据库指纹在 2651–2657 计算，隔离还原指纹在 2835–2842 比较，二者用于 **0012 备份证明**；源库此时没有新列 read_profile。实际 Recover 不重新把当前 0013 与旧库指纹比较，这也符合应保留新写入的要求。
3. `Assert-BoundedBackupReceipt`（2934–2981）验证首次备份的身份、verified 位及四份文件摘要，不能证明当前 0013 的列属性。现有 fingerprint 的 information_schema.columns 投影也未包含 collation。
4. Recover 在 3465–3468 读取实际 head、选 forward 并执行结构检查；随后 3483–3488 到 `Start-BoundedApplications`，3001 重新证明 guard、3003 恢复角色 LOGIN、3005 启动应用。该路径中不存在后续 read_profile collation 验证。完成态 Recover 的 3444 也复用同一结构函数。

建议：把迁移相关字符串列的实际 collation 与审定结构绑定，使用稳定名称及必要的行为属性，不能只检查索引与当前列互相一致。补一个真实 0013、非空数据、隐式 collation 漂移反例，并确认双 PowerShell 下拒绝且 Recover 保持维护、NOLOGIN 与数据。保留 R7 完整祖先检查、原叶文件可信主体、迁移固定摘要及既有还原/清理逻辑。

证据等级：缺失字段和完整调用链为静态确认；语义反例具有明确 PostgreSQL 机制依据，但本审计未执行 SQL，未验证该精确镜像的 ICU 构建选项，也未获得实际函数放行结果。现有真实语义漂移测试（`tests/integration/test_schema_upgrade_recovery.py:1771`）覆盖 CHECK 枚举/运输范围、谓词、INCLUDE、类型、NULL/default/NOT VALID，未覆盖隐式 collation。最终 bad_spec/其他分类由主线程依据最小复现决定。

## 输入和实际覆盖

- 完整读取 `docs/superpowers/specs/spec-bounded-schema-upgrade.md` 及其唯一 frontmatter context：`docs/superpowers/specs/spec-bounded-schema-upgrade-context.md`（153 行），包括冻结意图、全部 AC、授权/KEEP 和 R7/R6 补充。读取了 step-04 的 acceptance auditor 职责。
- 输入 `implementation.diff` 共 20621 行；独立 SHA256 核对为 `28dc646351acee3c9e2eca9abe2320370d2f819d75e383bf92c505e70ee385bd`。先索引所有文件/hunk，再按验收需求检查相关源码。没有声称逐行穷尽全部 20621 行，也未读取嵌入的其他审查发现正文。
- 完整阅读升级器 1551–4153 的有界路径：迁移边、标记、主 guard/辅助身份、任务宿主解析、结构/迁移镜像、角色/容器停写、实际备份还原、快照/辅助资产、环境/依赖身份、启动/健康/完成、跨进程失败清理、Apply/Recover 及外层入口。另读 588–829 的进程树/后台意图、948–995 的续租/失锁、1479–1518 的提交/审计路径。
- 检查 controller 的 Status/Plan/Apply 差异、安装器的固定 launcher 源码摘要与回执/备份差异、桌面入口的 Docker 启动前及双锁后标记检查、remote_maintenance 的标记拒绝差异；检查 hotfix 的 guard 调用点及 bootstrap 的 Create 前/持锁后/派发前标记调用。没有对这些旁路工具整文件的所有原有功能作重新认证。
- 完整阅读 R7 工具测试主 guard 正反例（3348–3501）、真实 guard fixture 和 ancestry-drift Recover 测试（2229–2397），及实际结构语义漂移测试（1747–1800）。R7 工具测试确实调用真实 NTFS guard，替换范围为夹具路径、夹具用户 SID 和任务枚举；真实 Recover 测试恢复实际 guard、保留首次收据绑定，并断言 NOLOGIN、停应用、active 标记、0013 和非空历史摘要。应用容器仍为隔离夹具，本审查未把它当作正式应用健康验收。
- 已读 R7 实施报告并逐项区分报告声明与可核验源码。发布树三文件 SHA256 与报告匹配：升级器 `d7ecf14c1134708c0948a02aec00f9e482183b88c40a37765c8105d170cfb626`；工具测试 `c98c3e46af8f4bda84fe1ce10e83c39579be21faed6e2399fc57ec7cf052e47b`；集成测试 `83cdc5a44485e89e99c4bde272ae29f6cbfee7313eb96cef1aeca56192ac4a8a`。
- 批准迁移文件实际 SHA256 为 `df20fff11e1b8ea04ef4ce69126c29021e221685ad27a138bbfafe10143ac5d1`，与固定门禁一致。launcher 规范化 LF 后源码 SHA256 为 `e72202e31595e1c9fe6143632d39befca0986414f4089a9d72d084103827057a`，与安装器审定值一致。

## 限制与未认证项

本轮仅做只读静态审查和本地摘要计算；未运行回归、Docker、数据库 SQL、远端预检或目标操作，未改源码/暂存区。仅新增本报告。

R7 报告所引用的完整工具/集成 `results.xml` 在获准发布树的相应路径下不存在；没有扩大访问范围去查根工作区日志。因此 179/179、4/4、44 容器状态和目标健康观测属于报告给出的既有证据声明，本审查没有独立重新认证这些执行记录。也未凭报告认证原 43 文件与批准快照的逐字节相等，或最终发布候选/现场采集通过。

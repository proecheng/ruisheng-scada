# R6 独立边界审查覆盖记录

输入：`D:\江苏润盛\tmp-test-logs\bounded-review-r6-2sNMq9\implementation.diff`。
输入 SHA256：`8a33f1d4475dbf0633d7fc22718da773d2bf002a754779f476b462d7425553a7`；恢复审查后再次核对一致。
只读项目：`C:\ProgramData\Ruisheng\publisher-build\bounded-schema-multidevice-20260908`。
最终问题清单：`edge-findings-final.json`，3 项，严格使用 location、trigger_condition、guard_snippet、potential_consequence 四字段。

状态：生产实现差异 hunks 的路径分析与复核已完成；整个 96 文件输入的所有测试与文档分支审查未全部完成。不得把本文件解释为全包逐分支审查通过。`edge-findings.json` 是原中间文件，本次未读取、未覆盖。

完整读取并遵循 `C:\Users\admin\.agents\skills\bmad-review-edge-case-hunter\SKILL.md`：接收冻结差异，追踪新增/修改 hunks 及其直接引用函数的分支，再复核遗漏边界，输出仅含未处理路径的 JSON。未运行审查输入中的命令、测试、构建、目标机命令、网络请求或数据库操作；未修改项目源码。

## 已完成范围

以下 37 个生产实现文件的差异 hunks 已完成路径分析及复核。关注范围包括控制流、空值/类型、配置代际、并发等待、超时/取消、持锁与失锁、持久化前后中断、恢复重复执行、ACL 主体与继承以及协议长度边界。

- `alembic/versions/20260907_0013_serial_polling_profile.py`
- `ruisheng-api/src/ruisheng_api/admin_bootstrap.py`
- `ruisheng-api/src/ruisheng_api/api/alarms.py`
- `ruisheng-api/src/ruisheng_api/api/devices.py`
- `ruisheng-api/src/ruisheng_api/api/points.py`
- `ruisheng-api/src/ruisheng_api/api/schemas/devices.py`
- `ruisheng-api/src/ruisheng_api/api/schemas/points.py`
- `ruisheng-api/src/ruisheng_api/core/config_changes.py`
- `ruisheng-api/src/ruisheng_api/db/repositories/devices.py`
- `ruisheng-gw/src/ruisheng_gw/domain/registry.py`
- `ruisheng-gw/src/ruisheng_gw/ingest.py`
- `ruisheng-gw/src/ruisheng_gw/main.py`
- `ruisheng-gw/src/ruisheng_gw/persistence/batch_writer.py`
- `ruisheng-gw/src/ruisheng_gw/protocol/framer.py`
- `ruisheng-gw/src/ruisheng_gw/scheduler/clock.py`
- `ruisheng-gw/src/ruisheng_gw/scheduler/poller.py`
- `ruisheng-gw/src/ruisheng_gw/scheduler/serial_poller.py`
- `ruisheng-gw/src/ruisheng_gw/transport/connection.py`
- `ruisheng-gw/src/ruisheng_gw/transport/serial_bus.py`
- `ruisheng-gw/src/ruisheng_gw/transport/session.py`
- `ruisheng-shared/src/ruisheng_shared/models/devices.py`
- `ruisheng-web/src/api/devices.ts`
- `ruisheng-web/src/layouts/AppLayout.vue`
- `ruisheng-web/src/utils/errors.ts`
- `ruisheng-web/src/views/devices/DeviceCreateView.vue`
- `ruisheng-web/src/views/devices/DeviceEditView.vue`
- `ruisheng-web/src/views/devices/DeviceListView.vue`
- `tools/install_ruisheng_desktop_launcher.ps1`
- `tools/probe_modbus_rtu.py`
- `tools/remote_admin_bootstrap.ps1`
- `tools/remote_full_upgrade.ps1`
- `tools/remote_full_upgrade/target-updater.ps1`
- `tools/remote_hotfix_deploy.ps1`
- `tools/remote_maintenance.ps1`
- `tools/remote_maintenance_prepare.ps1`
- `tools/run_modbus_probe.ps1`
- `tools/start_ruisheng_local.ps1`

仅在确认上述 hunks 的直接可达路径时只读相关函数定义。告警问题的补充引用为 `ruisheng-gw/src/ruisheng_gw/persistence/repository.py:98` 的 `apply_alarm_reading`；未延伸审查该文件其他无关逻辑。配置容量失败保留旧快照、失锁后拒绝推进、未知 Docker 意图拒绝猜测结果等已有显式处理的路径未列为问题。

## 问题触发路径与定位依据

1. `tools/remote_maintenance_prepare.ps1:242-249`：远端模板在有效的 `C:\Ruisheng\audit` 上执行 `-CreateAuditMutex`，若 `.remote-maintenance-audit.lock` 不存在，仅 `CreateNew`/`Dispose` 后返回。文件继承根目录的两条 ACE，继承仍启用，且没有当前调用者的独立 ACE。随后脚本返回 `prepared`。但 `tools/remote_full_upgrade/target-updater.ps1:198` 起的 mutex 分支与 `tools/remote_maintenance.ps1:508` 起的同类分支要求受保护的当前 SID/SYSTEM/Administrators ACL；后续 Plan/维护失败。同文件本机辅助函数 `:60-67` 有相同遗漏；未发现任何返回前或返回后补设该 mutex ACL 的路径。修复草图中的 `New-ProtectedMutexAcl` 表示新建的受保护文件 ACL 构造器，不是声称项目已有此函数。

2. `tools/remote_maintenance.ps1:532-541`：远端 `Assert-RestrictedFile` 对固定审计日志进入共享审计分支，只检查两条继承 ACE，随后 `return`，跳过原通用路径的 owner 验证。一个保留不受信任所有者、但 DACL 已继承正确根目录 ACE 的日志可以通过。NTFS 所有者仍具有修改 DACL 的能力；因此该边界不能仅以 ACE 检查证明日志不可篡改。本机 `Assert-SharedAuditFile` 的 `:111-123` 同样缺少 owner 校验。此问题以实际使用该分支的远程维护审计读写路径定位；未将没有实际日志调用点的辅助验证函数另算问题。

3. `ruisheng-gw/src/ruisheng_gw/ingest.py:163-180`：串口配置代际校验在调用 `apply_alarm_reading` 之前完成。该调用随后会等待数据库连接/事务；这期间停用、软删或修改点位/告警可完成提交和注册表刷新。仓储函数 `repository.py:98-129` 只按告警 ID、`c.enable` 查询，既不检查 `d.is_enabled`/`d.deleted_at`，也不接收并比对该帧使用的配置版本，因此仍可使用旧样本配合新配置触发告警。需要把版本条件及设备启用状态放入同一有设备锁保护的数据库事务；调用前或调用后的内存判断不能封闭这段等待窗口。

以上为静态路径证据，未执行复现实验。

## 未完成范围

以下 26 个测试/夹具文件未完成作为独立审查对象的全量逐分支分析。部分测试内容只作为实现路径旁证读取，不能替代完整测试质量或覆盖率审查。

- `ruisheng-api/tests/unit/api/test_devices_list.py`
- `ruisheng-api/tests/unit/api/test_serial_device_contract.py`
- `ruisheng-api/tests/unit/test_admin_bootstrap.py`
- `ruisheng-gw/tests/unit/test_batch_writer.py`
- `ruisheng-gw/tests/unit/test_clock.py`
- `ruisheng-gw/tests/unit/test_connection.py`
- `ruisheng-gw/tests/unit/test_ingest.py`
- `ruisheng-gw/tests/unit/test_poller.py`
- `ruisheng-gw/tests/unit/test_registry.py`
- `ruisheng-gw/tests/unit/test_serial_bus.py`
- `ruisheng-gw/tests/unit/test_serial_poller.py`
- `ruisheng-gw/tests/unit/test_session_frame.py`
- `ruisheng-shared/tests/test_models_devices.py`
- `ruisheng-web/e2e/devices.spec.ts`
- `ruisheng-web/e2e/fixtures/serialDevices.ts`
- `ruisheng-web/tests/unit/api/devices.test.ts`
- `ruisheng-web/tests/unit/utils/errors.test.ts`
- `tests/integration/test_admin_bootstrap.py`
- `tests/integration/test_schema_upgrade_recovery.py`
- `tests/integration/test_serial_device_lifecycle.py`
- `tests/tools/test_desktop_launcher.py`
- `tests/tools/test_modbus_probe.py`
- `tests/tools/test_release_verification_receipt.py`
- `tests/tools/test_remote_admin_bootstrap.py`
- `tests/tools/test_remote_full_upgrade.py`
- `tests/tools/test_remote_operations.py`

其余 17 个非历史审查文档文件未完成完整文档边界分析：`deploy/setup-customer.md`、`docs/REMOTE_DEBUG.md`、输入中的 6 个 `docs/reports/*.md`、输入中审查目录以外的 8 个 `docs/superpowers/specs/*.md`、`ruisheng-shared/src/ruisheng_shared/CHANGELOG.md`。部分说明仅用于确认实现的已声明边界。

## 为独立性排除

输入中 `docs/superpowers/specs/bounded-schema-upgrade-reviews/` 下的 16 个历史 findings/classification 文件按任务要求不作为审查依据、不读取其审查结论。未读取同轮其他审查者输出，也未读取本轮中间问题清单。目录、文件名及 diff 标题仅用于识别排除范围。

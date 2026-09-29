# R4 review classification

日期：2026-09-09

输入：`tmp-test-logs/bounded-review-r4-oPgaVq/implementation.diff`，SHA256 `be3aaa55f1d7bd8b371922bc789b3d1d7de480ca532c408ec2398c3282acaf14`，86个文件、17,099行、958,012字节；发布HEAD `5d83607bc6d5bf90234ac713aa5dab6b96eb3461`，原baseline保持不变。主线程核对86文件摘要及反向应用检查通过。原始三路报告见21、22、23号文件。

## bad_spec（触发 R5 重推导）

- `tools/remote_full_upgrade/target-updater.ps1`：动态 `ScriptBlock.Create(...).Invoke()` 和无法解析的实例成员调用落入默认安全分支。
- `tools/remote_full_upgrade/target-updater.ps1`：PowerShell `& .\\wrapper` 省略 `.ps1` 扩展名时未检查实际脚本解析路径。
- `tools/remote_full_upgrade/target-updater.ps1`：`cscript.exe`、`wscript.exe`、`mshta.exe` 等未受支持脚本宿主未被拒绝或固定身份证明。

这些发现直接违反既有“维护期间所有业务启动入口必须可证明”的非冻结实施约束；冻结意图、迁移边和数据保护要求保持不变。按 step-04，已在规格非冻结部分补充 R5 闭集规则，进入重新实施和新一轮审查。

## defer（不进入本轮重推导）

- 129 个串口地址导致整个 Registry 刷新失败：属于既有多设备实现，已记录到 `docs/superpowers/specs/deferred-work.md`。
- 迟到同形 RTU 响应误关联：属于既有协议边界，已记录到 deferred-work。
- 首次审计目录/互斥文件 ACL 继承问题：属于既有维护准备入口，已记录到 deferred-work。
- `remote_admin_bootstrap.ps1` 在 `Status=empty` 后保留锁、无法重试：属于既有 bootstrap 行为，已记录到 deferred-work。
- 多页设备列表 offset 在并发删除时遗漏：属于既有 43 文件多设备快照范围，未修改本轮升级器。

## reject

未发现需要丢弃的噪声项。审查探针只调用判断函数，未执行危险样例、任务、Docker、数据库或设备操作。

## 主线程证据与保留边界

`probe-startup-r4-main.ps1` 从冻结升级器加载真实判断函数，在PowerShell5.1/7.6.4中均确认实例Process.Start、动态ScriptBlock、extensionless、cscript、wscript、mshta六种输入返回false；受保护启动器返回false，直接docker start返回true。危险命令均仅作数据。另在独有审查目录创建只含Write-Output的脚本，以两版PowerShell实际CLI省略扩展名执行成功，证明该路径解析不是猜测。

R4源文件和测试结果保持原历史边界：最终165项工具和2项wrapper真实恢复通过，但这些测试未覆盖此次漏检。未推送、提交、签名、上传、安装或迁移生产。R5仅重推导违例的启动入口块；43文件多设备快照、批准迁移摘要、停写/恢复/快照/审计、已部署兼容和固定辅助身份继续KEEP，不整包撤销工作区。

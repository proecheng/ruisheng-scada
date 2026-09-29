# 目标机SSH现场恢复工具

**现场已执行，远程登录已恢复。** 9 月 21 日 14:39 现场人员运行恢复包，14:40:36 维护端成功登录固定目标 WIN-OAUCM8UQUGH。公钥文件与 ACL 已验证，现场不需要再手工编辑。备份位于 `C:\ProgramData\Ruisheng\ssh-access-recovery\20260921-143909-b84e21f65d004122b2bafa4da902ac26`。读取的 before 记录可能来自重复执行，不能唯一判断最初认证失败原因。15:23 已部署串口重连修复，详见[发布报告](2026-09-21-serial-reconnect-digital-state.md)。以下操作说明保留用于交接，本轮无需重复运行。

## 现场操作

将`ssh-recovery-WIN-OAUCM8UQUGH-20260921.zip`复制到目标电脑，先解压。右键`START-RESTORE.cmd`，选择“以管理员身份运行”，在Windows确认窗口点“是”。不需要现场人员编辑配置或填写公钥。

完成后保持窗口，告知维护人员“已运行”。同目录`RESULT.txt`保存结果；出现`NOT COMPLETE`时，将里面的文字提供给维护人员，不要继续自行改权限。管理员身份确认需要现场具备该电脑的管理员权限。

`REPAIR COMPLETE`只表示公钥文件和ACL已完成本机验证，远程登录仍须由维护端复核。登录恢复后继续检查实际运行版本、USB映射和DI/DO数据，按既有受控流程发布此前已测试的修复。

## 工具范围

- 仅允许在固定目标主机以管理员身份执行；从`sshd -T -C`读取适用于`lenovo`和当前支持端地址的配置，识别管理员公钥文件或用户公钥文件。不猜测自定义文件路径，也不自动更改SSH配置。
- 使用原有公钥，指纹为`SHA256:dE1iYX3saiUfnlRvatcaFyIswTi5qQ4qMXGPne5pIaI`；包内无私钥、密码或令牌。若缺少该公钥，仅追加限制来源为`100.67.229.19`的条目；原有同一公钥的附加限制及其他公钥均保留。
- 保留主机、remote-support权益、既有维护锁检查。预检使用Windows Job限制40秒、256MiB、各输出65536字符，并只读取最多12条近期OpenSSH日志。
- 备份公钥原始字节和原ACL后，规范文件编码及文件权限；管理员文件只保留SYSTEM/Administrators，用户文件额外允许`lenovo`。失败时尝试恢复原始字节与ACL。备份保留在`C:\ProgramData\Ruisheng\ssh-access-recovery\`下的时间戳目录。
- 不修改SSH主机密钥、密码登录设置、防火墙和账户状态，不重启sshd、采集软件、Docker或电脑。若服务、自定义配置、权限或权益不符合预期，则停止并记录。

该工具处理常见公钥缺失、文件编码和ACL故障，不能预先断言现场认证失败一定由这些原因造成。命令限制、账户策略等其他原因仍可能需要结合结果进一步调查。

## 本地验证

在Windows PowerShell 5.1和PowerShell 7分别运行临时文件测试，22项全部通过，验证原有受限公钥保留、仅追加指定支持公钥、重复执行幂等、编码修复、配置路径选择与异常拒绝、真实NTFS权限、字节和ACL回退，以及误在其他主机运行时提前拒绝。所有文件测试均使用临时目录，没有操作本机实际SSH授权文件。结果保存于`tmp-test-logs/reconnect-digital-20260921/ssh-recovery-tests.txt`。

相关文件：`tools/restore_target_ssh_access.ps1`、`tools/restore_target_ssh_access.cmd`、既有`tools/diagnostic_limits.ps1`与`tests/tools/test_restore_target_ssh_access.py`。目标机实际执行和远程登录复核尚待完成。

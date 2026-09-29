# 2026-09-28 桌面启动器误报与恢复复核

现场截图显示启动器在“Verifying the active release”阶段弹出 `compose_manifest_image_mismatch`。该截图对应旧入口的校验逻辑：它只接受候选 manifest 中的镜像身份，未接受已经合法留存的热修复 manifest、归档 SHA-256 和 Docker image ID；同一恢复路径还只把串口缺失退出码 255 当作可恢复，目标实际出现过 128。

已在独立修改后部署并保留目标机旧启动器、guard receipt 和安装备份。当前安装入口 `C:\Program Files\Ruisheng\Launcher\start_ruisheng_local.ps1` 的 SHA-256 为 `f141ac7e31f2e49cf6c17987ace3ceb22c492b5e3437982dd0878ad066b26608`，与源码一致。修复包含热修复 manifest/归档/image ID 校验、退出码 128/255 识别、热修复目录 ACL 兼容和安装器白名单同步。

目标机 `WIN-OAUCM8UQUGH/100.109.90.21` 的复核快照为 [`startup-recheck-20260928T080551394Z.json`](../../tmp-test-logs/field-acceptance-20260916/snapshots/startup-recheck-20260928T080551394Z.json)，SHA-256 为 `e550768cacc96bba2f52b4261bc521864ee003f1166ac9559c8fc21433626a83`。快照显示活动版本 `deploy-20260922.1`、源码 `68fd3a90df64185ac38c23953b81201040919d43`，五项服务全部运行，Web 返回 HTTP 200，DEV001 38 点完整且无空值；最近一小时 132 个完整轮次，估算缺轮 6。维护锁已释放。

启动器和串口恢复回归为 **51 passed、2 skipped**；跳过项是需要显式启用的真实 Docker 保留资产测试。状态历史已追加 `desktop-launcher-startup-repair-20260928`，原始 `observation-state.json` 备份保留在 `observation-state.pre-startup-repair-20260928.bak`。

这次修复解决的是平台启动入口误报和串口缺失恢复判定。最近 24 小时仍包含启动故障造成的最长约 14050 秒历史缺口和约 3124 个估算缺轮，不能据此宣称连续 24 小时通过；恢复后仍有普通串口超时，继续按采集板固件/RS485 转换器和接线边界取证，不无依据调整重试或超时。
## 旧弹窗进程清理后的复核

截图对应的旧启动器进程已按精确路径匹配关闭（PID 11224），没有重启或修改 Docker 服务。随后重新采集的 [`startup-post-stale-process-20260928T081733107Z.json`](../../tmp-test-logs/field-acceptance-20260916/snapshots/startup-post-stale-process-20260928T081733107Z.json) SHA-256 为 `44c283cd7234dd94fa7a86492eb48d77041992f9422b54a30e120ce4cf1897b6`：五项服务仍运行，活动版本和源码不变，DEV001 38 点完整无空值，最近一小时 270 个完整轮次、估算缺轮 7，未再发现启动器进程。现场现在可以重新双击“润盛监控系统”快捷方式，调用已修复入口。
## 16:22 失败后的桌面快捷方式复测

现场在 16:22 看到 `desktop_launcher_failed`，对应进程在弹窗后保持不退出；受保护检查确认当时 Docker 服务仍健康。已关闭该卡住实例（PID 19036），随后直接启动目标机快捷方式 `C:\Users\lenovo\Desktop\润盛监控系统.lnk`，启动器审计记录为 `launcher_completed / already_ready`，错误码为空，约 20 秒后入口进程正常退出。

快捷方式复测后的快照为 [`startup-shortcut-post-verify-20260928T083322624Z.json`](../../tmp-test-logs/field-acceptance-20260916/snapshots/startup-shortcut-post-verify-20260928T083322624Z.json)，SHA-256 为 `65d731393944bc78c5c1e86a384091e64b29745c484bfc8ca6940e230d990be4`。五项服务运行，DEV001 38 点完整无空值，最近一小时 457 个完整轮次、估算缺轮 9；活动版本仍为 `deploy-20260922.1`，维护锁释放。

因此当前可复现结论是：启动器在清理卡住实例后，目标桌面快捷方式能够返回 `already_ready`；原 16:22 弹窗是一次瞬态卡住失败，审计中没有留下可验证的更细子错误。若现场下一次仍出现新错误，应保留弹窗时间和对应窗口，不要反复点击，以便按同一时间段读取审计记录。
## 17:55 重启后复核（2026-09-28）

目标机 `WIN-OAUCM8UQUGH/100.109.90.21` 重启后重新上线。`remote_maintenance.ps1 -Action Status` 观察到活动发布 `deploy-20260922.1`，源码 `68fd3a90df64185ac38c23953b81201040919d43`，五项服务均运行且维护锁均不存在；随后用既有受限入口采集快照 [`startup-reboot-status-20260928T095449794Z.json`](../../tmp-test-logs/field-acceptance-20260916/snapshots/startup-reboot-status-20260928T095449794Z.json)，SHA-256 为 `53944f7a4106e81275d165eff0de869cc4cf2fbb404d5c9993e0b62905527959`。

重启后最近一小时有 708 个完整 38 点轮次、0 空值，估算缺轮 6；最近 24 小时有 14068 个完整轮次、0 空值，但仍含约 14050 秒历史长缺口和 3136 个估算缺轮。日志窗口为 4000 行并已截断，不能把日志事件数当作完整窗口总数。该复核证明重启后的服务和采集已恢复，不证明启动器新一次双击没有弹窗；若现场仍看到 `desktop_launcher_failed`，应保留本次弹窗时间和新截图，才能与审计记录一一对应。

状态文件已追加 `desktop-launcher-post-reboot-verification-20260928`，原文件备份为 `observation-state.pre-startup-reboot-20260928-1755.bak`。

## 当前安装入口哈希补充

当前工作区受保护安装入口 `tools/start_ruisheng_local.ps1` 的 SHA-256 为 `a1940251fce08604f24d70994fec85f4e395423806b3882391693ac033353dc5`；此前记录的 `f141ac7e...` 保留为历史安装阶段哈希，不覆盖历史证据。目标机本次重启复核沿用已部署的修复入口，未发现维护锁残留或服务未启动。

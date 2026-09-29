# R3 独立边界审查提示

在没有本项目对话历史的新审查会话中执行，使用与主会话相同的模型能力。仅只读审查，不修改项目、不提交、不访问目标机、不操作 Docker、不启动其他代理。已有审查因模型服务额度不足中断，没有有效通过结论。

使用并完整读取 `C:\Users\admin\.agents\skills\bmad-review-edge-case-hunter\SKILL.md`，按其步骤枚举改变行直接可达的分支和边界。不要阅读其他审查员的结果，不把已有处理的路径或假设性风险列为问题。

输入差异：`D:\江苏润盛\tmp-test-logs\bounded-review-r3-n3TAnU\implementation.diff`。

SHA256：`e565e348793ad14d3c4028d4ce890d140f28ea96f24c754b1194a3c93d9348c0`；81 个文件、16,425 行、894,550 bytes。分段有界读取并处理截断，确认覆盖输入，不要重复输出整个差异。

仅在差异明确引用外部函数、需要核实可达性时，允许只读访问发布工作树 `C:\ProgramData\Ruisheng\publisher-build\bounded-schema-multidevice-20260908`。不要读取其他根工作区的项目内容。允许安全的内存推理，不启动项目程序或实际数据库/系统故障实验。

只报告证据支持的未处理路径。按技能输出严格 JSON 数组，每项只有 `location`、`trigger_condition`、`guard_snippet`、`potential_consequence` 四个字段，并遵守字段长度和格式。没有发现可返回 `[]`；覆盖未完成则明确报告无法完成审查，不能用空数组表示通过。

结果仅可用 apply_patch 写入 `D:\江苏润盛\tmp-test-logs\bounded-review-r3-n3TAnU\edge-findings.json`，也可直接返回。不得修改任何冻结输入。

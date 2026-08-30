---
title: 'Docker 29 镜像归档描述符兼容校验'
type: 'bugfix'
created: '2026-08-22'
status: 'done'
baseline_commit: 'ceb8084ce605edcd8e1dc436f20bb571c37811dd'
context:
  - 'docs/superpowers/specs/spec-plan-5-b03-release-artifacts.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Docker 29 在 `docker save` 归档的顶层 OCI `index.json` 中可能同时写入主镜像描述符和 SLSA provenance referrer；当前三个离线校验器强制顶层只能有一个描述符，导致内容与身份均正确的候选版本在归档复检阶段失败。

**Approach:** 从顶层描述符中精确解析出唯一连接到 `manifest.json` 配置摘要的主镜像，再仅接受结构、摘要、subject、平台配置和 in-toto layer 全部符合约束的 provenance 附件；Python、PowerShell、Shell 校验路径保持同一接受/拒绝语义。

## Boundaries & Constraints

**Always:** 主描述符必须唯一解析到 legacy `manifest.json` 所指配置；所有实际读取的描述符、配置和 layer blob 均校验 SHA-256；附加描述符只能是 OCI manifest，其 `io.containerd.manifest.subject` 必须指向主镜像的唯一可运行 manifest，附件配置必须为 `os=unknown`、`architecture=unknown`，且只能包含可验证的 `application/vnd.in-toto+json` provenance layer；三个校验器对同一归档得出相同结论。

**Ask First:** 若真实 Docker 归档需要接受其他附件类型、多个可运行平台、非 SHA-256 摘要、缺失 subject 的兼容形式，或需要改变候选 manifest 中 `image_id` 的定义，停止并请求批准。

**Never:** 不跳过或弱化摘要、blob、平台、标签、配置身份检查；不把任意第二描述符当作可忽略元数据；不修改旧候选清单、不上传候选、不部署目标机，也不把 provenance 的存在宣称为已建立可信签名或来源真实性。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Docker 29 合法归档 | 一个唯一主描述符，加一个 subject 指向主 manifest、unknown 平台配置且仅含 in-toto provenance layer 的 referrer | 返回原主镜像 ID、OS、架构并允许继续候选验证 | N/A |
| 普通或多平台主镜像 | 顶层仅主描述符，或主索引仅导出选中平台 blob | 保持现有解析与身份结果 | N/A |
| 额外真实镜像 | 两个描述符均可解析到镜像配置，或主身份不唯一 | 拒绝归档 | 在加载前报告主描述符不唯一 |
| 恶意或损坏附件 | subject 错误、未知 media type、非 unknown 附件配置、非 in-toto/多余 layer、blob 缺失或摘要不符 | 拒绝归档 | 在加载前报告具体附件约束失败 |

</frozen-after-approval>

## Code Map

- `tools/release_artifacts.py` -- 构建时和包验证时使用的权威 Docker 归档身份解析器。
- `deploy/verify-candidate.ps1` -- Windows 目标机加载前的独立归档校验实现。
- `deploy/verify-candidate.sh` -- Linux/Unix 目标机加载前嵌入式 Python 校验实现。
- `tests/tools/test_release_artifacts.py` -- 合成归档的正常、兼容与恶意输入回归测试。
- `tests/tools/test_release_artifacts_docker.py` -- 真实 Docker 生成、验证和加载闭环。

## Tasks & Acceptance

**Execution:**
- [x] `tests/tools/test_release_artifacts.py` -- 构造 Docker 29 顶层 provenance referrer，并覆盖合法通过、第二主镜像、错误 subject、未知附件、平台/layer 违规、缺失 blob 和摘要篡改。
- [x] `tools/release_artifacts.py` -- 将主描述符识别与严格 provenance 附件验证拆成可审计的边界，保留现有单镜像及选中平台行为。
- [x] `deploy/verify-candidate.ps1` / `deploy/verify-candidate.sh` -- 同步实现相同描述符分类和拒绝规则，确保目标机预检不会比构建端宽松。
- [x] `tests/tools/test_release_artifacts_docker.py` -- 用 Docker 29 真实归档证明候选生成、三端验证及加载后身份核验闭环。

**Acceptance Criteria:**
- Given 当前提交的五类镜像和 Docker 29，when 生成并验证 `deploy-20260822.1`，then 合法 provenance 附件不再阻断，五个归档身份、Compose 和候选 manifest 仍精确一致。
- Given 合成归档包含任一未知或被篡改的附加描述符，when 任一校验器执行加载前检查，then 校验失败且不调用 `docker load`。
- Given 无顶层附件的历史归档或仅导出所选平台 blob 的源索引，when 执行新校验器，then 既有兼容行为和 `image_id` 结果不变。

## Spec Change Log

## Design Notes

附加 provenance manifest 不是主镜像身份。主镜像仍以唯一能连到 legacy 配置摘要的顶层描述符为准；附件的 subject 必须指向该主描述符解析出的可运行 manifest，而不是仅指向顶层 index。附件自身的 config 与 layer 也属于不可信输入，必须逐个验证存在性、摘要和结构后才可忽略其身份影响。

## Verification

**Commands:**
- `uv run pytest tests/tools/test_release_artifacts.py -q` -- expected: 合成归档接受/拒绝矩阵全部通过。
- `uv run ruff check tools/release_artifacts.py tests/tools/test_release_artifacts.py tests/tools/test_release_artifacts_docker.py` -- expected: lint 通过。
- `bash -n deploy/verify-candidate.sh && pwsh -NoProfile -Command "[void][scriptblock]::Create((Get-Content -Raw deploy/verify-candidate.ps1))"` -- expected: 两个目标机脚本语法通过。
- `$env:RUN_RELEASE_DOCKER_E2E='1'; uv run pytest tests/tools/test_release_artifacts_docker.py -q` -- expected: Docker 29 真实生成、验证、加载与篡改拒绝闭环通过。
- `ENV_FILE=.env.prod.example bash deploy/export-images.sh deploy-20260822.1 linux/amd64` 后运行候选 `verify` 与 `verify --load` -- expected: 新候选完整生成并通过本地验证，不上传、不部署。

## Suggested Review Order

**权威归档边界**

- 从唯一主镜像识别入口理解描述符分类和身份保持。
  [`release_artifacts.py:541`](../../../tools/release_artifacts.py#L541)

- 严格绑定顶层附件、unknown 配置和单个 SLSA layer。
  [`release_artifacts.py:457`](../../../tools/release_artifacts.py#L457)

- 校验 in-toto statement 结构及主 manifest subject。
  [`release_artifacts.py:350`](../../../tools/release_artifacts.py#L350)

- 拒绝主索引内第二个实际可运行镜像。
  [`release_artifacts.py:380`](../../../tools/release_artifacts.py#L380)

**目标机一致性**

- Windows 端收紧 JSON 类型、空 blob 和 provenance 语义。
  [`verify-candidate.ps1:151`](../../../deploy/verify-candidate.ps1#L151)

- PowerShell 加载前入口与权威解析保持同一分类结果。
  [`verify-candidate.ps1:329`](../../../deploy/verify-candidate.ps1#L329)

- Linux 端嵌入式 Python 镜像化同一拒绝规则。
  [`verify-candidate.sh:95`](../../../deploy/verify-candidate.sh#L95)

**回归证据**

- 合成 Docker 29 归档覆盖合法和恶意描述符矩阵。
  [`test_release_artifacts.py:214`](../../../tests/tools/test_release_artifacts.py#L214)

- PowerShell 对同一攻击矩阵逐项验证拒绝语义。
  [`test_release_artifacts.py:928`](../../../tests/tools/test_release_artifacts.py#L928)

- 真实 Docker 29 五镜像闭环确认 provenance、加载和篡改阻断。
  [`test_release_artifacts_docker.py:109`](../../../tests/tools/test_release_artifacts_docker.py#L109)

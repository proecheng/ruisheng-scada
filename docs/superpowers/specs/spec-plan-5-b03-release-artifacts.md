---
title: 'Plan 5 B-03 不可变离线候选制品'
type: 'feature'
created: '2026-08-19'
status: 'done'
baseline_commit: '4b88a856f8d4fa251d7232acb4bbc18adad41204'
context:
  - 'docs/superpowers/specs/spec-plan-5-customer-deployment-acceptance/SPEC.md'
  - 'docs/superpowers/specs/spec-plan-5-customer-deployment-acceptance/deployment-contract.md'
  - 'docs/superpowers/specs/spec-plan-5-customer-deployment-acceptance/acceptance-matrix.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** 当前流程使用应用 `latest` 和可漂移 Redis 标签，仅执行 `docker save | gzip`；没有候选 ID、不可变镜像身份、manifest、SHA-256 或加载后核验，无法证明 Compose、归档与提交一致。

**Approach:** 使用显式候选 ID/目标平台在干净提交上生成独立 staging 包；五类镜像重标为候选标签，记录源与候选身份，并生成同源双 manifest、严格校验和及跨平台验证脚本。

## Boundaries & Constraints

**Always:** 输出到新的 `dist/deploy/<candidate-id>/`，成功前仅写临时目录；五个唯一归档中 `migrate/api` 共用 API；候选 Compose 无 build/pull 且标签精确；manifest 记录 ID、完整 commit、OS/arch、Alembic head、工具版本及每镜像的源引用、RepoDigest（可空）、候选标签、image ID、归档/SHA；校验拒绝缺失、额外、重复、越界和身份/平台不符。

**Ask First:** 选择签名工具、发布身份、信任锚、密钥保管或可信分发机制；改变候选命名/保留策略；支持多平台单包或远端 registry 推送时立即停止请求批准。

**Never:** 不把 SHA 称为签名，不宣布 CAP-1/G0-03/Plan 5 完成；不实现管理员引导、网络、恢复、Profile、SBOM、可复现 gzip、GitHub Release 或 `FROM` digest 加固；不现场修改基础 Compose。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| 正常生成 | 唯一 ID、显式平台、干净 HEAD、五类镜像可解析 | 原子生成完整候选；标签、ID、平台、归档和 Compose 一致 | N/A |
| 不安全输入 | ID 非法/复用、相关 tracked 输入脏、平台混装、标签碰撞 | 不产生可交付目录 | 非零退出并清理临时输出 |
| 包被篡改 | 缺失/额外文件、SHA 错误、路径越界/重复 | 加载前拒绝 | 不执行 `docker load` |
| 镜像不匹配 | 加载后标签解析到错误 ID/OS/arch，或 Compose 集合漂移 | 验证失败 | 输出字段级差异，不启动服务 |
| 未配置签名 | manifest 与 SHA 自洽但无批准的可信来源 | 仅报告完整性通过 | 明确真实性与 G0-03 仍 BLOCKED |

</frozen-after-approval>

## Code Map

- `deploy/export-images.sh` / `tools/release_artifacts.py` -- 导出入口与候选核心。
- `docker-compose.prod.yml` / `deploy/docker-compose.prod.yml` / `.env.prod.example` -- 镜像输入及离线契约。
- `deploy/setup-customer.md` / `deploy/verify-candidate.{sh,ps1}` -- 目标机校验与阻断说明。
- `tests/tools/test_release_artifacts.py` / `tests/tools/test_production_compose.py` -- 生成、验证和 Compose 回归。

## Tasks & Acceptance

**Execution:**
- [x] `tools/release_artifacts.py` -- 实现元数据、原子 staging、双 manifest、SHA allowlist 与安全验证，并支持测试注入命令执行器。
- [x] `deploy/export-images.sh` -- 要求 ID/平台和干净输入，构建/拉取、重标五类镜像后导出；失败清理半包。
- [x] `docker-compose.prod.yml` / `deploy/docker-compose.prod.yml` / `.env.prod.example` / `deploy/.env.prod.example` -- 参数化 PostgreSQL/Redis，移除应用 `latest` 默认；生成包写入五个精确候选标签，离线所有服务禁止拉取。
- [x] `deploy/verify-candidate.sh` / `deploy/verify-candidate.ps1` / `deploy/setup-customer.md` -- 严格校验后加载并核对标签/ID/平台/Compose；说明签名门槛，禁止编辑基础 Compose。
- [x] `tests/tools/test_release_artifacts.py` / `tests/tools/test_production_compose.py` -- 用 fake Docker/合成归档覆盖生成、共享 API、重用/脏输入、碰撞、半包、篡改、路径及身份漂移。

**Acceptance Criteria:**
- Given 干净候选输入，when 生成并验证离线包，then 五个归档、候选 Compose、双 manifest 与 SHA 字段精确闭环，且重复解析同一输入得到同一逻辑身份。
- Given 任一文件或镜像身份被改变，when 在目标机运行验证，then 校验在启动前失败并指出差异，不访问 registry、不构建、不启动服务。
- Given 没有获批签名/可信分发，when 完整性校验通过，then 输出仍明确 CAP-1/G0-03 BLOCKED，不产生真实性声明。

## Spec Change Log

## Design Notes

候选目录同时生成 `MANIFEST.json` 与由同一数据模型渲染的 `MANIFEST.md`，自动化只解析结构化格式。每个实际归档有独立 SHA；不要求两次 gzip 字节一致。基础镜像源标签可用于拉取，但候选 Compose 只使用包内重标后的候选标签。

## Verification

**Commands:**
- `uv run pytest tests/tools/test_release_artifacts.py tests/tools/test_production_compose.py -q` -- expected: 契约通过。
- `uv run ruff check tools/release_artifacts.py tests/tools/test_release_artifacts.py tests/tools/test_production_compose.py` -- expected: lint 通过。
- `bash -n deploy/export-images.sh deploy/verify-candidate.sh` -- expected: shell 语法通过。
- `pwsh -NoProfile -Command "[void][scriptblock]::Create((Get-Content -Raw deploy/verify-candidate.ps1))"` -- expected: PowerShell 语法通过。
- 用五个本地小镜像执行生成、篡改拒绝与加载后核验 -- expected: 正常包通过，篡改包失败且无半包。

## Suggested Review Order

**候选生成与原子性**

- 从唯一入口理解锁、构建、归档、复检和原子发布。
  [`release_artifacts.py:991`](../../../tools/release_artifacts.py#L991)

- 流式导出从进程启动计时，失败不留下双份制品。
  [`release_artifacts.py:134`](../../../tools/release_artifacts.py#L134)

**完整性与离线验证**

- 严格闭环 allowlist、SHA、归档身份和 Compose 服务映射。
  [`release_artifacts.py:839`](../../../tools/release_artifacts.py#L839)

- PowerShell 在任何加载前解析 legacy/OCI Docker 归档身份。
  [`verify-candidate.ps1:106`](../../../deploy/verify-candidate.ps1#L106)

- 离线 Compose 对六个服务锁定候选镜像、平台和禁止拉取。
  [`docker-compose.prod.yml:4`](../../../deploy/docker-compose.prod.yml#L4)

**客户操作与真实性边界**

- 安装和升级复核站点 env，同时保留密钥访问控制。
  [`setup-customer.md:126`](../../../deploy/setup-customer.md#L126)

**回归证据**

- 合成归档覆盖碰撞、半包、漂移、篡改和类型边界。
  [`test_release_artifacts.py:234`](../../../tests/tools/test_release_artifacts.py#L234)

- 真实 Docker 29 闭环生成、加载、PowerShell 预检和篡改拒绝。
  [`test_release_artifacts_docker.py:45`](../../../tests/tools/test_release_artifacts_docker.py#L45)

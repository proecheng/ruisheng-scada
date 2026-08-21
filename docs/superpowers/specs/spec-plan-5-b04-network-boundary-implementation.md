---
title: 'B-04 网络边界控制实现'
type: 'feature'
created: '2026-08-20'
status: 'in-review'
baseline_commit: 'f5611a9588a5eaf9bb5f5f49960d74295a539ba2'
context:
  - 'D:/江苏润盛/docs/superpowers/specs/spec-plan-5-b04-network-boundary/SPEC.md'
  - 'D:/江苏润盛/docs/superpowers/specs/spec-plan-5-b04-network-boundary/network-control-matrix.md'
  - 'D:/江苏润盛/docs/superpowers/specs/spec-plan-5-customer-deployment-acceptance/deployment-contract.md'
---

<frozen-after-approval reason="human-owned intent - do not modify unless human renegotiates">

## Intent

**Problem:** B-04 当前把 Web、GW 设备端口和 GW 管理端口以无站点绑定约束的方式发布；GW health 固定监听 `0.0.0.0`；Nginx 代理全部 `/api/`，且默认访问日志可能记录 `/ws?token=` 中的 JWT。健康、就绪和指标端点也没有应用鉴权，无法证明管理面只对批准监控主体开放。

**Approach:** 为生产 Compose 引入显式、默认回环的 Web/GW 绑定参数；让 GW health host 可配置；在 Web 镜像内默认隔离健康端点并使用不含查询字符串的访问日志；提供受控站点 health ACL 示例和候选/站点配置验证器，锁定无数据库/Redis/API 直曝、无 host network、无通配绑定旁路和令牌日志泄漏，并为这些边界补自动化测试。

## Boundaries & Constraints

**Always:** 基础 Compose 与 Nginx 配置保持可校验；PostgreSQL、Redis、API 不发布宿主机端口；Web、`gw:5020`、`gw:9090` 的绑定地址和端口来自站点环境；默认值只允许回环；健康端点必须有独立 ACL；访问日志不得记录完整请求目标或令牌；站点 TLS、CIDR 和防火墙仍由批准 Profile 决定。

**Ask First:** 若现场要求共享/不受信网络访问，必须先提供批准的 HTTPS/WSS 终止方案、证书和旁路防护；若需要把管理探针开放给非回环监控网段，必须提供 Profile 中的监控 CIDR 与独立 ACL 文件；不得用代码默认值替代这些决定。

**Never:** 不在候选基础文件中写入现场 CIDR、证书私钥或客户地址；不直接发布 API、PostgreSQL、Redis；不使用 `host` network 或 `0.0.0.0`/`::` 作为默认绑定；不把 HTTP 或 WS 当作共享网络安全传输；不通过关闭鉴权测试来“通过”管理面验收。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| CLOSED_DEFAULT | 环境仅含模板回环绑定值 | Compose 只监听回环 Web/GW 端口；内部服务无宿主机端口 | validator PASS；现场需改站点值并重新审批 |
| WILDCARD_BIND | 任一发布地址为 `0.0.0.0`、`::` 或空值 | 配置验证拒绝通配绑定 | 明确提示需批准等效 ACL，退出非零 |
| HEALTH_BYPASS | 用户路径请求 `/api/health/ready` | 普通 Web 路由不能转发管理探针 | 403/404；监控只能通过 site ACL |
| TOKEN_LOG | WebSocket 请求含 `?token=secret` | 访问日志不含查询字符串或 token | 测试扫描发现 token 即失败 |
| PUBLIC_INTERNAL | Compose 为 API、PostgreSQL 或 Redis 添加 `ports`/`host` network | validator 拒绝 | 列出服务和违规字段，退出非零 |

</frozen-after-approval>

## Code Map

- `docker-compose.prod.yml` -- 构建生产 Compose 的显式宿主机绑定和站点网络变量。
- `deploy/docker-compose.prod.yml` -- 离线候选的等价网络绑定与安全默认值。
- `.env.prod.example`, `deploy/.env.prod.example` -- 绑定变量和受控网络模式模板。
- `ruisheng-gw/src/ruisheng_gw/config.py`, `ruisheng-gw/src/ruisheng_gw/main.py` -- 可配置 health host 并传入 aiohttp site。
- `ruisheng-web/nginx.conf`, `ruisheng-web/site-health-acl.conf`, `ruisheng-web/Dockerfile` -- 健康端点 ACL、结构化日志脱敏和默认策略文件。
- `ruisheng-api/src/ruisheng_api/__main__.py`, `ruisheng-api/tests/unit/test_entrypoint.py` -- 清除 Uvicorn WebSocket 日志中的查询参数并验证专用令牌不泄漏。
- `deploy/site-health-acl.conf.example`, `deploy/site-network.override.yml`, `docs/superpowers/specs/spec-plan-5-customer-deployment-acceptance/site-acceptance-profile.md` -- 可执行的站点 ACL/override 与可机器校验的批准 Profile 字段。
- `tools/validate_network_boundary.py`, `tools/release_artifacts.py`, `deploy/verify-candidate.*` -- 实际 Compose 渲染验证器和候选包 B-04 材料交付/完整性契约。
- `tests/tools/test_network_boundary.py`, `tests/tools/test_release_artifacts.py`, `ruisheng-gw/tests/unit/test_config.py` -- Compose/ACL/Profile/日志/候选交付与 GW health host 回归测试。
- `tests/tools/test_production_compose.py` -- 两套 Compose 与环境模板等价性回归断言。
- `deploy/setup-customer.md` -- 绑定、ACL、Profile 和 TLS 前置条件说明。

## Tasks & Acceptance

**Execution:**
- [x] 为根/离线 Compose 和两个环境模板增加显式 Web/GW 宿主绑定变量，默认回环；容器内 GW health listener 使用 Docker 可达的 IP 字面量，同时保持所有非批准服务无宿主发布。
- [x] 为 GW `health_host` 增加 IP 字面量校验并传入 aiohttp site；覆盖空值、hostname、IPv4、IPv6 和容器通配监听测试。
- [x] 更新 Nginx/API 日志配置：精确及子路径 health 路由均受独立 ACL 控制，Nginx access/error 与 Uvicorn WebSocket 日志均不记录查询参数、Authorization、Cookie 或 Referer 中的令牌。
- [x] 提供只读 site ACL mount 的站点 Compose override；让 ACL allow CIDR 与 Profile 批准的监控 CIDR完全一致，并拒绝 `all`、默认路由和缺少最终 deny 的策略。
- [x] 实现网络边界验证器：只验证实际由一个或多个 Compose 文件渲染的对象，校验精确服务集合、全部 ports/expose/network_mode/外部网络、listener/target、ACL mount/source、Nginx 结构、完整审批/Profile 网络字段及 TLS/旁路决定。
- [x] 将 ACL、override、Profile 模板、Nginx 配置和 validator 纳入不可变候选及完整性校验；把 network validator 接入文档化的每次启动/回滚前流程。
- [x] 补工具、Compose、候选包、GW/API 日志及 Nginx 运行测试，并运行后端/工具/Web、静态检查、构建与 Nginx 语法检查。

**Acceptance Criteria:**
- Given 模板环境，when 渲染生产 Compose，then Web/GW 只绑定模板回环地址，API、PostgreSQL、Redis 没有宿主机端口。
- Given 任一宿主通配绑定、未知/内部服务端口、expose、host/container network mode 或未批准外部网络，when 运行 validator，then 命令非零并指出违规服务/字段。
- Given 默认 Nginx 配置，when 请求 `/api/health`、其子路径或携带专用 query token 的 WebSocket，then health 路径不走普通业务代理，Nginx/Uvicorn 日志均不包含 query string/token。
- Given GW 在 Compose 中启动，when 宿主机发布 9090，then 容器 listener 使用可由 Docker 转发命中的 IP 字面量，宿主发布仍只绑定批准的具体地址，且 target/listener 端口一致。
- Given Web 绑定非回环地址，when Profile 未明确批准隔离可信 HTTP 或 HTTPS/WSS 终止及无旁路控制，then validator 保持 BLOCKED；批准值与渲染绑定不一致时 FAIL。
- Given site ACL，when allow CIDR 不是 Profile 批准的监控 CIDR、策略允许全网、未只读挂载或校验文件不是实际 mount source，then validator FAIL/BLOCKED。
- Given 站点 ACL/配置未批准或缺失，when 运行 validator，then B-04 保持 BLOCKED，不能被模板默认值静默放行。

## Verification

**Commands:**
- `uv run pytest tests/tools/test_network_boundary.py tests/tools/test_production_compose.py tests/tools/test_release_artifacts.py ruisheng-gw/tests/unit/test_health.py ruisheng-gw/tests/unit/test_config.py ruisheng-api/tests/unit/test_entrypoint.py` -- expected: all pass.
- `uv run ruff check tools/validate_network_boundary.py ruisheng-gw/src/ruisheng_gw/config.py ruisheng-gw/src/ruisheng_gw/main.py` -- expected: clean.
- `docker compose --env-file deploy/.env.prod.example -f deploy/docker-compose.prod.yml -f deploy/site-network.override.yml config --format json` -- expected: valid JSON with no unresolved variables and a read-only ACL bind mount.

## Spec Change Log

- 2026-08-20 review loop 1：三路审查发现初版只做文本 marker 与部分端口检查，未证明 ACL 被实际挂载、Profile/ACL/绑定一致、候选包含验证材料，也混淆了容器 listener 与宿主发布地址；同时 Nginx 脱敏未覆盖 Referer/error log 和 Uvicorn WebSocket 日志。已扩充 Code Map、执行任务和验收条件，明确实际多文件 Compose 渲染、完整暴露枚举、可机器校验 Profile、TLS/旁路 gate、候选交付及运行时日志验证。避免继续保留“伪 Profile + 手工 rendered JSON 即 PASS”和“容器 loopback 9090 不可达”的已知坏状态。KEEP：保留默认回环宿主绑定、内部 API/PostgreSQL/Redis 不发布、独立 health 路由、`$uri` 安全访问日志方向及现有完整回归基线。
- 2026-08-20 review loop 2：复审发现 GW 9090 的源 ACL 未与 Profile 绑定、非回环 TLS/可信 HTTP 证据可用任意文本伪造、用户/设备/外部/未批准 CIDR 未解析、日常重启前缺少 gate，以及新增未跟踪发布输入未纳入 source integrity。已增加 `GW_HEALTH_ALLOWED_CIDRS` 与 aiohttp 源 ACL、Profile 全字段 CIDR/TLS 证据校验、安全/合规审批、脚本条件 gate、symlink/UTF-8 错误处理和全量 release input 检查。KEEP：保留 loop 1 的实际 Compose 渲染、只读 mount/source 比对、精确 health location、Uvicorn query 清除和候选材料 allowlist。
- 2026-08-20 review loop 3：复审要求非回环 Web 传输证据不能由任意关键词或复述性文字伪造。已将 `HTTPS_WSS`/`TRUSTED_HTTP` 证据改为显式 `key=value` 字段，强制终止点、证书引用、域名、隔离/防火墙标识及 direct HTTP/WS 策略；Nginx 日志策略额外拒绝 User-Agent/remote-user，ACL 路径在 POSIX 上拒绝组/其他用户可写，并补齐相关回归测试与部署说明。

## Suggested Review Order

1. **验收一致性审查**：对照冻结 Intent、Boundaries、Acceptance Criteria，确认默认宿主绑定仍为回环、默认 Profile/ACL 未决时 validator 必须 `BLOCKED`，且没有通过模板值静默放行。
2. **盲安全审查**：从实际多文件 Compose 渲染开始，核对服务集合、`ports`/`expose`/`network_mode`/网络驱动、三组宿主发布、GW health 源 ACL、Nginx 精确 health 路径、只读 ACL mount 和所有日志格式。
3. **边界与异常审查**：注入缺字段、非法 CIDR、默认路由、IPv4/IPv6、非回环传输伪造证据、ACL 替换/符号链接、未知服务和未跟踪候选输入，确认稳定 `FAIL/BLOCKED` 而非 traceback 或误报 PASS。
4. **执行证据审查**：复跑后端/工具测试、前端 Vitest/ESLint/构建、Ruff/mypy、Compose JSON 渲染和 Nginx `-t`；最后核对候选完整性、`git diff --check` 及工作区中未授权文件。

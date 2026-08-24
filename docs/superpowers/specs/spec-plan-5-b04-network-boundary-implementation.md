---
title: 'B-04 网络边界控制实现'
type: 'feature'
created: '2026-08-20'
status: 'done'
baseline_commit: 'bf28cbb228316a2ea79f565ab53edb8f7718fbd4'
context:
  - 'D:/江苏润盛/docs/superpowers/specs/spec-plan-5-b04-network-boundary/SPEC.md'
  - 'D:/江苏润盛/docs/superpowers/specs/spec-plan-5-b04-network-boundary/network-control-matrix.md'
  - 'D:/江苏润盛/docs/superpowers/specs/spec-plan-5-customer-deployment-acceptance/deployment-contract.md'
---

<frozen-after-approval reason="human-owned intent - do not modify unless human renegotiates">

## Intent

**Problem:** B-04 当前把 Web、GW 设备端口和 GW 管理端口以无站点绑定约束的方式发布；GW health 固定监听 `0.0.0.0`；Nginx 代理全部 `/api/`，且默认访问日志可能记录 `/ws?token=` 中的 JWT。健康、就绪和指标端点也没有应用鉴权，无法证明管理面只对批准监控主体开放。

**Approach:** 为生产 Compose 引入显式、默认回环的 Web/GW 绑定参数；让 GW health host 可配置；在 Web 镜像内默认隔离健康端点并使用不含查询字符串的访问日志；以来源 ACL 和独立 Bearer 管理凭据共同保护 API/GW 管理端点，只向应用注入站点高熵令牌的 SHA-256 摘要并执行常量时间比较；提供受控站点 health ACL 示例和候选/站点配置验证器，锁定无数据库/Redis/API 直曝、无 host network、无通配绑定旁路和令牌日志泄漏，并为这些边界补自动化测试。

## Boundaries & Constraints

**Always:** 基础 Compose 与 Nginx 配置保持可校验；PostgreSQL、Redis、API 不发布宿主机端口；Web、`gw:5020`、`gw:9090` 的绑定地址和端口来自站点环境；默认值只允许回环；管理端点必须同时通过独立来源 ACL 和 Bearer 管理凭据，生产环境缺失或非法摘要时启动失败；访问日志不得记录完整请求目标、Authorization 或令牌；站点 TLS、CIDR、防火墙、凭据摘要及保管/轮换责任仍由批准 Profile 决定。

**Ask First:** 若现场要求共享/不受信网络访问，必须先提供批准的 HTTPS/WSS 终止方案、证书和旁路防护；若需要把管理探针开放给非回环监控网段，必须提供 Profile 中的监控 CIDR、独立 ACL 文件及批准的凭据生成/交接/轮换方案；不得用代码默认值替代这些决定。

**Never:** 不在候选基础文件、Git、镜像、Compose 环境、日志或共享证据中写入管理令牌明文、现场 CIDR、证书私钥或客户地址；不直接发布 API、PostgreSQL、Redis；不使用 `host` network 或 `0.0.0.0`/`::` 作为默认绑定；不把 HTTP 或 WS 当作共享网络安全传输；不接受短令牌、可逆密钥表示、非 SHA-256 摘要或通过关闭鉴权测试来“通过”管理面验收。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| CLOSED_DEFAULT | 环境仅含模板回环绑定值和未决凭据摘要 | Compose 只监听回环 Web/GW 端口；内部服务无宿主机端口 | validator BLOCKED；批准 Profile 和真实摘要齐备前不得启动 |
| WILDCARD_BIND | 任一发布地址为 `0.0.0.0`、`::` 或空值 | 配置验证拒绝通配绑定 | 明确提示需批准等效 ACL，退出非零 |
| HEALTH_BYPASS | 用户路径或 Docker hairpin 请求 `/api/health/ready`，无/错 Bearer 凭据 | 即使 Docker 把来源改写成批准网关也拒绝 | 403；监控必须同时通过 site ACL 和凭据校验 |
| AUTH_INVALID | 生产环境摘要缺失、含占位符、非 64 位小写十六进制或 Compose/API/GW/Profile 不一致 | 配置验证或应用启动失败 | 明确报错但不得输出令牌或可复用 Authorization |
| TOKEN_LOG | WebSocket 请求含 `?token=secret` | 访问日志不含查询字符串或 token | 测试扫描发现 token 即失败 |
| PUBLIC_INTERNAL | Compose 为 API、PostgreSQL 或 Redis 添加 `ports`/`host` network | validator 拒绝 | 列出服务和违规字段，退出非零 |

</frozen-after-approval>

## Code Map

- `docker-compose.prod.yml` -- 构建生产 Compose 的显式宿主机绑定和站点网络变量。
- `deploy/docker-compose.prod.yml` -- 离线候选的等价网络绑定与安全默认值。
- `.env.prod.example`, `deploy/.env.prod.example` -- 绑定变量和受控网络模式模板。
- `ruisheng-gw/src/ruisheng_gw/config.py`, `ruisheng-gw/src/ruisheng_gw/main.py` -- 可配置 health host 并传入 aiohttp site。
- `ruisheng-api/src/ruisheng_api/api/health.py`, `ruisheng-api/src/ruisheng_api/config.py`, `ruisheng-gw/src/ruisheng_gw/health.py` -- API/GW 管理端点的 Bearer 摘要认证、常量时间比较与生产 fail-closed 配置。
- `ruisheng-web/nginx.conf`, `ruisheng-web/site-health-acl.conf`, `ruisheng-web/Dockerfile` -- 健康端点 ACL、结构化日志脱敏和默认策略文件。
- `ruisheng-api/src/ruisheng_api/__main__.py`, `ruisheng-api/tests/unit/test_entrypoint.py` -- 清除 Uvicorn WebSocket 日志中的查询参数并验证专用令牌不泄漏。
- `deploy/site-health-acl.conf.example`, `deploy/site-network.override.yml`, `docs/superpowers/specs/spec-plan-5-customer-deployment-acceptance/site-acceptance-profile.md` -- 可执行的站点 ACL/override，以及区分原始监控来源、Docker NAT 后容器观察来源和固定 bridge 网关的批准 Profile 字段。
- `tools/validate_network_boundary.py`, `tools/release_artifacts.py`, `deploy/verify-candidate.*` -- 实际 Compose 渲染验证器和候选包 B-04 材料交付/完整性契约。
- `tests/tools/test_network_boundary.py`, `tests/tools/test_network_boundary_docker.py`, `tests/tools/test_release_artifacts.py`, `ruisheng-gw/tests/unit/test_config.py` -- Compose/ACL/Profile/日志/候选交付、GW health host 和真实 Docker NAT 来源回归测试。
- `tests/tools/test_production_compose.py` -- 两套 Compose 与环境模板等价性回归断言。
- `deploy/setup-customer.md` -- 绑定、ACL、Profile 和 TLS 前置条件说明。

## Tasks & Acceptance

**Execution:**
- [x] 为根/离线 Compose 和两个环境模板增加显式 Web/GW 宿主绑定变量，默认回环；容器内 GW health listener 使用 Docker 可达的 IP 字面量，同时保持所有非批准服务无宿主发布。
- [x] 为 GW `health_host` 增加 IP 字面量校验并传入 aiohttp site；覆盖空值、hostname、IPv4、IPv6 和容器通配监听测试。
- [x] 更新 Nginx/API 日志配置：精确及子路径 health 路由均受独立 ACL 控制，Nginx access/error 与 Uvicorn WebSocket 日志均不记录查询参数、Authorization、Cookie 或 Referer 中的令牌。
- [x] 提供只读 site ACL mount 的站点 Compose override；让 Web/GW 应用 ACL 与 Profile 批准的“容器观察来源”完全一致，并拒绝 `all`、默认路由、整个 Docker bridge 网段和缺少最终 deny 的策略；原始监控 CIDR 仍由宿主绑定、防火墙或批准入口控制，不信任客户端可写代理头恢复源地址。
- [x] 为应用 bridge 配置站点可批准的固定子网和网关；验证器区分原始监控来源、NAT 后容器观察来源，且只允许精确 bridge 网关主机路由或未被 NAT 的批准监控 CIDR进入应用 ACL。
- [x] 实现网络边界验证器：只验证实际由一个或多个 Compose 文件渲染的对象，校验精确服务集合、全部 ports/expose/network_mode/外部网络、固定 IPAM、listener/target、ACL mount/source、Nginx 结构、完整审批/Profile 网络字段及 TLS/旁路决定。
- [x] 将 ACL、override、Profile 模板、Nginx 配置和 validator 纳入不可变候选及完整性校验；把 network validator 接入文档化的每次启动/回滚前流程。
- [x] 为 API/GW 管理端点实现独立 Bearer 管理凭据：站点只向容器注入同一高熵令牌的 SHA-256 摘要，生产缺失/非法摘要时启动失败，无/错/畸形 Authorization 恒为 403，比较使用常量时间且日志/错误不泄漏凭据；validator 必须锁定 env、Compose 和批准 Profile 的方案/摘要一致性。
- [x] 补工具、Compose、候选包、GW/API 日志及 Nginx 运行测试；真实 Docker 测试必须由生产 Compose、站点 ACL override 和最小测试 overlay 启动实际 GW/Web 镜像与环境接线，证明全部管理路径从宿主发布端口经精确 bridge 网关得到预期响应、同网络非批准容器（含伪造代理头和宿主 hairpin 尝试）得到 403/不可达，并在容器重启后复测；最后运行后端/工具/Web、静态检查、构建与 Nginx 语法检查。Windows Docker Desktop 实测证明宿主携带正确令牌时 Web/GW 全部管理路径返回 200；同 bridge 容器直连、伪造代理头、摘要冒充令牌和被改写成网关来源的 hairpin 均为 403/不可达，重启后结果保持。

**Acceptance Criteria:**
- Given 模板环境，when 渲染生产 Compose，then Web/GW 只绑定模板回环地址，API、PostgreSQL、Redis 没有宿主机端口。
- Given 任一宿主通配绑定、未知/内部服务端口、expose、host/container network mode 或未批准外部网络，when 运行 validator，then 命令非零并指出违规服务/字段。
- Given 默认 Nginx 配置，when 请求 `/api/health`、其子路径或携带专用 query token 的 WebSocket，then health 路径不走普通业务代理，Nginx/Uvicorn 日志均不包含 query string/token。
- Given API/GW 在生产 Compose 中启动，when 管理凭据摘要缺失、非法或 API/GW 接线不一致，then 配置验证或进程启动失败；原始管理令牌只由批准监控主体保管，不进入 Compose、镜像、Git、日志或共享证据。
- Given GW 在 Compose 中启动，when 宿主机通过批准的 9090 发布端口携带正确 Bearer 凭据探测，then 容器 listener 使用可由 Docker 转发命中的 IP 字面量、应用 ACL 接受经批准映射后的精确 bridge 网关来源且凭据摘要匹配后返回 200，宿主发布仍只绑定批准的具体地址，且 target/listener 端口一致。
- Given Web 绑定非回环地址，when Profile 未明确批准隔离可信 HTTP 或 HTTPS/WSS 终止及无旁路控制，then validator 保持 BLOCKED；批准值与渲染绑定不一致时 FAIL。
- Given Web/GW 管理 ACL，when allow CIDR 不是 Profile 批准的容器观察来源、不是未转换的批准监控 CIDR或渲染后精确 Docker 网关主机路由、覆盖整个 bridge、策略允许全网、未只读挂载或校验文件不是实际 mount source，then validator FAIL/BLOCKED。
- Given Windows Docker Desktop NAT 和生产 Compose 测试 overlay，when 宿主回环探针携带正确 Bearer 凭据、同 bridge 普通容器不持有凭据并分别请求 Web/GW 全部管理端点且重启后复测，then 宿主探针通过固定网关映射与凭据双重校验成功，普通容器的直连、伪造代理头、无/错凭据和被 NAT 成网关的 hairpin 尝试均得到 403/不可达；不得通过向普通容器注入管理凭据、允许整个 bridge、Nginx real-ip/proxy-protocol 重写或信任 `X-Forwarded-For` 实现。
- Given 站点 ACL/配置未批准或缺失，when 运行 validator，then B-04 保持 BLOCKED，不能被模板默认值静默放行。

## Verification

**Commands:**
- `uv run pytest tests/tools/test_network_boundary.py tests/tools/test_production_compose.py tests/tools/test_release_artifacts.py ruisheng-gw/tests/unit/test_health.py ruisheng-gw/tests/unit/test_config.py ruisheng-api/tests/unit/test_main.py ruisheng-api/tests/unit/test_config.py ruisheng-api/tests/unit/core/test_management_auth.py ruisheng-api/tests/unit/test_entrypoint.py` -- expected: all pass.
- `uv run ruff check tools/validate_network_boundary.py ruisheng-gw/src/ruisheng_gw/config.py ruisheng-gw/src/ruisheng_gw/main.py` -- expected: clean.
- `docker compose --env-file deploy/.env.prod.example -f deploy/docker-compose.prod.yml -f deploy/site-network.override.yml config --format json` -- expected: valid JSON with no unresolved variables and a read-only ACL bind mount.
- Linux: `B04_DOCKER_E2E=1 uv run pytest tests/tools/test_network_boundary_docker.py -m integration`; Windows PowerShell: `$env:B04_DOCKER_E2E='1'; uv run pytest tests/tools/test_network_boundary_docker.py -m integration` -- expected: production Compose GW/Web management paths accept host NAT, reject peer/direct/forged/hairpin access, and retain the result after restart.

## Spec Change Log

- 2026-08-20 review loop 1：三路审查发现初版只做文本 marker 与部分端口检查，未证明 ACL 被实际挂载、Profile/ACL/绑定一致、候选包含验证材料，也混淆了容器 listener 与宿主发布地址；同时 Nginx 脱敏未覆盖 Referer/error log 和 Uvicorn WebSocket 日志。已扩充 Code Map、执行任务和验收条件，明确实际多文件 Compose 渲染、完整暴露枚举、可机器校验 Profile、TLS/旁路 gate、候选交付及运行时日志验证。避免继续保留“伪 Profile + 手工 rendered JSON 即 PASS”和“容器 loopback 9090 不可达”的已知坏状态。KEEP：保留默认回环宿主绑定、内部 API/PostgreSQL/Redis 不发布、独立 health 路由、`$uri` 安全访问日志方向及现有完整回归基线。
- 2026-08-20 review loop 2：复审发现 GW 9090 的源 ACL 未与 Profile 绑定、非回环 TLS/可信 HTTP 证据可用任意文本伪造、用户/设备/外部/未批准 CIDR 未解析、日常重启前缺少 gate，以及新增未跟踪发布输入未纳入 source integrity。已增加 `GW_HEALTH_ALLOWED_CIDRS` 与 aiohttp 源 ACL、Profile 全字段 CIDR/TLS 证据校验、安全/合规审批、脚本条件 gate、symlink/UTF-8 错误处理和全量 release input 检查。KEEP：保留 loop 1 的实际 Compose 渲染、只读 mount/source 比对、精确 health location、Uvicorn query 清除和候选材料 allowlist。
- 2026-08-20 review loop 3：复审要求非回环 Web 传输证据不能由任意关键词或复述性文字伪造。已将 `HTTPS_WSS`/`TRUSTED_HTTP` 证据改为显式 `key=value` 字段，强制终止点、证书引用、域名、隔离/防火墙标识及 direct HTTP/WS 策略；Nginx 日志策略额外拒绝 User-Agent/remote-user，ACL 路径在 POSIX 上拒绝组/其他用户可写，并补齐相关回归测试与部署说明。
- 2026-08-24 review loop 4：现场验收证明 Windows Docker Desktop 会把宿主 `127.0.0.1:9090` 请求转换为容器对端 `172.18.0.1`，而原规格强制 GW/Web 应用 ACL 等于逻辑监控 CIDR，导致静态校验可 PASS、三个 GW 管理端点运行时却全部 403。现将原始监控来源、Docker NAT 后容器观察来源和固定 bridge 网关分开建模，并要求真实 Docker 正向/反向测试。避免“临场加入动态 `172.18.0.0/16` bridge 网段”“信任客户端代理头”以及“只跑纯 Python fixture 即宣称可用”的已知坏状态。KEEP：保留默认回环宿主绑定、API/PostgreSQL/Redis 不发布、精确 health 路由、最终 deny、只读实际 mount、查询令牌日志脱敏、Profile 未决时 BLOCKED 和四类来源现场门禁。
- 2026-08-24 review loop 5：复审发现首版 Docker E2E 使用手工 `docker run` 和内联 health app，未证明生产 Compose、GW Config/main 接线和 Web ACL mount；Nginx validator 也未禁止 real-ip 源重写。现要求以生产 Compose + 站点 override + 最小测试 overlay 启动实际镜像，覆盖 GW/Web 全管理路径、伪造代理头、同 bridge 直连、宿主 hairpin 和重启复测；同时补应用子网可用性、站点网段冲突、bridge NAT 选项及精确网关约束。避免“测试自建一套正确网络而生产 Compose 接线仍错误”和“通过 `real_ip_header` 把客户端头改写为允许来源”的已知坏状态。KEEP：保留 loop 4 的原始/观察来源分层、固定网关主机路由、真实 Docker 200/403 证据，以及此前所有默认拒绝、完整性和日志保护约束。
- 2026-08-24 人工批准的平台修正：真实 Windows Docker Desktop E2E 证明，同 bridge 容器直连 Web/GW 为 403，但经 `host.docker.internal` hairpin 后会与宿主探针一样被改写为固定 bridge 网关并返回 200；Windows 本机监听也只观察到 Docker 代理后的本机来源，因此单一源 ACL 无法满足冻结的 hairpin 拒绝条件。经用户明确批准，将管理面改为“源 ACL + 独立 Bearer 管理凭据”双重门禁，生产只注入 SHA-256 摘要并要求常量时间比较，E2E 必须证明 hairpin 即使呈现允许网关也因无凭据被拒绝。KEEP：保留全部源 ACL、固定 IPAM、Profile、TLS、日志、端口和现场正反向探测要求，不把认证当作放宽网络控制的替代品。

## Suggested Review Order

**认证门禁**

- 缺失摘要直接拒绝，并以常量时间比较合法令牌。
  [`api/management_auth.py:41`](../../../ruisheng-api/src/ruisheng_api/core/management_auth.py#L41)

- API 三个管理路由统一挂载 Bearer 依赖。
  [`api/health.py:26`](../../../ruisheng-api/src/ruisheng_api/api/health.py#L26)

- GW 先校验真实对端来源，再校验独立 Bearer 凭据。
  [`gw/health.py:88`](../../../ruisheng-gw/src/ruisheng_gw/health.py#L88)

- 配置错误隐藏输入，防止误填明文进入启动日志。
  [`gw/config.py:29`](../../../ruisheng-gw/src/ruisheng_gw/config.py#L29)

**部署契约**

- Compose 只向 API/GW 注入同一摘要并锁定生产模式。
  [`docker-compose.prod.yml:72`](../../../docker-compose.prod.yml#L72)

- Validator 绑定实际渲染、固定 IPAM、ACL、Profile 和摘要。
  [`validate_network_boundary.py:400`](../../../tools/validate_network_boundary.py#L400)

- 凭据轮换强制重建容器并验证新旧令牌结果。
  [`setup-customer.md:79`](../../../deploy/setup-customer.md#L79)

**执行证据**

- 真实生产入口覆盖六路径、直连、hairpin 和重启复测。
  [`test_network_boundary_docker.py:159`](../../../tests/tools/test_network_boundary_docker.py#L159)

- 负向测试覆盖改名明文令牌和配置旁路。
  [`test_network_boundary.py:199`](../../../tests/tools/test_network_boundary.py#L199)

- 新发现的首次数据库启动竞态已隔离记录。
  [`deferred-work.md:11`](deferred-work.md#L11)

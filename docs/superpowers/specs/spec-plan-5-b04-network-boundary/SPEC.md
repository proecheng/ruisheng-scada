---
id: SPEC-plan-5-b04-network-boundary
companions:
  - network-control-matrix.md
  - ../spec-plan-5-customer-deployment-acceptance/SPEC.md
  - ../spec-plan-5-customer-deployment-acceptance/deployment-contract.md
  - ../spec-plan-5-customer-deployment-acceptance/acceptance-matrix.md
  - ../spec-plan-5-customer-deployment-acceptance/site-acceptance-profile.md
sources: []
---

> **Canonical contract.** This SPEC and the files in `companions:` are the complete, preservation-validated contract for B-04 implementation and acceptance. Consulted live project files remain implementation inputs and are mapped in `.decision-log.md`.

# Plan 5 B-04 网络边界与传输安全

## Why

Plan 5 的网络安全门槛尚未获得可验证证据：生产 Compose 将 Web、GW 设备端口和 GW 管理端口发布到宿主机，Nginx 代理全部 `/api/`，API 与 GW 的健康/指标端点没有应用鉴权，GW 管理服务固定监听所有地址。项目必须在客户环境开放前，把用户、设备、运维和内部服务流量限制到批准路径，并证明认证、令牌和控制流量不会通过共享或不受信网络明文传输或从旁路入口进入。

## Capabilities

- id: CAP-1
  intent: 运维人员可以依据已批准的站点网络参数部署默认拒绝、最小暴露的服务边界。
  success: 渲染后的 Compose、宿主机监听和防火墙证据共同证明，仅批准的用户入口、设备入口和管理入口从对应源网段可达；API、PostgreSQL 和 Redis 无宿主机外部入口，未批准的 IPv4/IPv6 路径均不可达。

- id: CAP-2
  intent: 用户可以通过唯一受控入口安全地完成认证、实时通信和设备控制。
  success: 认证、令牌、WebSocket 和控制流量经过共享或不受信网络时仅使用 HTTPS/WSS，证书和主机名校验通过，HTTP 或直接地址不能绕过保护；访问令牌和刷新令牌不进入代理、应用或验收日志。

- id: CAP-3
  intent: 批准的监控主体可以读取运行探针和指标，而其他主体无法访问管理面。
  success: API 的 live/ready/metrics 和 GW 的 health/ready/metrics 仅从 Profile 批准的运维/监控路径并携带独立管理凭据时可达，用户、设备、未批准网段和无/错凭据的 Docker hairpin 探测均被拒绝；来源 ACL 与管理凭据必须同时满足，任一控制不得替代另一个。

- id: CAP-4
  intent: 发布和运维人员可以在不修改候选基础文件的前提下应用并校验站点网络差异。
  success: 所有绑定地址、端口、TLS、ACL 和管理路径差异均来自受控站点 Profile/override；候选基础文件校验保持有效，配置渲染会拒绝未决参数、未批准发布端口、无等效边界控制的通配绑定和 TLS 旁路。

- id: CAP-5
  intent: 验收人员可以从每个相关网段复现网络边界结论并形成可审计证据。
  success: 用户、设备、运维/监控和未批准网段分别执行正向与反向探测，覆盖 DNS/直接地址、HTTP/HTTPS、WS/WSS、设备端口、管理端点及启用的 IPv4/IPv6；结果与同一 Profile 版本、候选 ID、提交和镜像摘要关联，全部适用项为 PASS。

## Constraints

- `site-acceptance-profile.md` 的网络、TLS、监控、防火墙和探测位置必须在现场配置或验收前由项目负责人、运维负责人和客户代表批准；任何适用字段为 `UNRESOLVED` 时 B-04 保持 BLOCKED。
- Web 只向批准的用户网络开放；认证、令牌、WebSocket 或控制流量经过共享或不受信网络时必须使用 HTTPS/WSS，明文入口不得承载这些流量或形成可绕过的直达路径。
- `gw:5020` 只向批准的设备网络开放，`gw:9090` 只向批准的运维/监控网络开放；设备协议不被假定自带传输加密，跨共享或不受信链路必须有批准的隔离或加密控制。
- API、PostgreSQL 和 Redis 只能在应用内部网络被所需服务访问，不得通过宿主机端口、host network、未受控代理或等效路径直接暴露。
- API `/api/health/live`、`/api/health/ready`、`/api/health/metrics` 与 GW `/health`、`/ready`、`/metrics` 必须同时使用来源 ACL 和独立 Bearer 管理凭据；生产环境只接收批准站点高熵令牌的 SHA-256 摘要，缺失或非法摘要必须 fail closed，明文令牌不得进入 Git、镜像、Compose 环境、日志或共享验收证据。
- WebSocket 当前通过查询参数携带访问令牌；在协议改变或代理日志明确去除查询参数并通过泄漏测试前，不得启用会持久化完整请求目标的访问日志。
- 签名候选的基础 Compose 和 Nginx 配置不得在现场直接编辑；站点差异必须位于独立、受控、可校验并可恢复的 Profile/override 中，秘密不得进入 Git、镜像或验收附件。
- Docker 端口发布与宿主机防火墙必须同时检查 IPv4 和 IPv6；未启用的地址族须有显式禁用证据，不能以未执行探测代替。
- 真实客户环境、防火墙、DNS、证书、生产监听或路由变更需要单独明确授权；本规格本身不授权实施这些变更。

## Non-goals

- 本规格不替客户决定 CIDR、域名、证书颁发机构、防火墙产品、监控平台或责任人。
- 本规格不把 API/GW 健康端点改造成面向普通用户的功能，也不授权将 API、PostgreSQL 或 Redis 直接发布到宿主机。
- 本规格不授权生产上线、真实设备控制、真实通知供应商启用、客户数据迁移或任何未批准的外部扫描。
- 本规格完成不等于 B-04 解锁；只有已批准 Profile、实际控制和跨网段证据齐备后才能关闭阻断项。

## Success signal

使用同一不可变候选和已批准站点 Profile，四类来源的探测证明所有批准路径可用、所有未批准路径不可达、共享或不受信路径上的认证与控制仅经 HTTPS/WSS、管理端点仅供批准监控主体访问，且配置与日志中没有旁路或令牌泄漏；B-04 证据经项目、运维和客户代表签署。

## Assumptions

- 客户机继续采用当前 Docker Compose 部署模型，站点网络差异通过独立 override 和宿主机/上游网络控制表达。
- 容器内部 Web 到 API、API/GW 到 PostgreSQL/Redis 的通信位于受控主机内部网络；若实际部署跨主机，该假设失效并须重新定义服务间加密与认证。
- `gw:5020` 的设备协议只能在批准的隔离设备网络中按明文运行；需要跨共享或不受信网络时使用经批准的 VPN、专线或等效加密隔离。

## Open Questions

- 用户、设备、运维/监控、外部服务和明确不批准的 IPv4/IPv6 网段分别是什么？
- Web 是否仅在隔离可信网使用 HTTP，还是使用 HTTPS/WSS；批准的域名、证书来源、信任链、TLS 终止点、续期和失效处置是什么？
- 如何禁止绕过 TLS 终止点直接访问 Web HTTP、API 容器地址或其他宿主机接口？
- Web、`gw:5020` 和 `gw:9090` 分别绑定哪个宿主机地址与端口，是否存在 NAT、负载均衡、双网卡或反向代理？
- API/GW 的 live、ready 和 metrics 分别允许哪些监控主体访问，采用源 ACL、独立管理入口、mTLS、认证代理还是其他控制？
- 防火墙或网络 ACL 由哪个平台执行、谁负责配置和复核，容器重建与主机重启后如何保证规则仍然生效？
- IPv4 和 IPv6 是否同时启用；若禁用一种地址族，在哪一层禁用并如何取证？
- 用户、设备、运维/监控和未批准网段的验收探测分别从哪些实际位置执行？
- WebSocket 查询令牌采用协议调整还是日志去查询参数方案，如何证明所有日志和验收附件均未记录令牌？

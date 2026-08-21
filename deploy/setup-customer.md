# 江苏润盛 SCADA — 部署说明

## 前提条件

- Windows 10/11（PowerShell 7.3+）或 Ubuntu 20.04+
- 已安装 [Docker Desktop](https://www.docker.com/products/docker-desktop/)
  （Windows 用户安装 Docker Desktop；Linux 用户安装 Docker Engine + Docker Compose v2）
- Linux 校验脚本需要系统自带的 `python3`
- 80、5020、9090 端口未被占用
- 内存：建议 4 GB 以上

## 部署步骤

### 1. 校验候选并加载镜像

将完整候选目录复制到目标机器，在候选目录中打开终端。不要编辑、删除或添加候选目录中的任何文件；站点密钥和配置保存在候选目录外。

当前候选提供 SHA-256、归档身份和加载后镜像身份校验，但尚未配置获批的发布签名或可信分发机制。SHA-256 只能证明所收到文件之间自洽，不能证明发布者身份；即使脚本成功，CAP-1/G0-03 仍为 BLOCKED。

**Windows（PowerShell）：**
```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\verify-candidate.ps1 .
```

**Linux/Mac（Terminal）：**
```bash
bash ./verify-candidate.sh .
```

脚本严格检查文件 allowlist、`SHA256SUMS`、五个归档、目标平台、候选标签、镜像 ID 及离线 Compose。任何缺失、额外、重复、路径越界、篡改或身份漂移都会在启动前失败；脚本不会访问 registry、构建或启动服务。此时站点 ACL/Profile 尚未创建，脚本会明确输出 B-04 `BLOCKED` 并以退出码 `2` 结束；这是预期结果，不能把它当作可启动信号，继续完成下一步站点配置。

### 2. 配置环境变量

在候选目录的同级位置建立受控站点目录，复制模板并填写密码。不要修改候选中的 `.env.prod.example` 或 `docker-compose.prod.yml`：

**Linux/Mac：**
```bash
mkdir -p ../site
cp .env.prod.example ../site/.env.prod
```

**Windows PowerShell：**
```powershell
New-Item -ItemType Directory -Force ..\site | Out-Null
Copy-Item .env.prod.example ..\site\.env.prod
```

用文本编辑器打开 `../site/.env.prod`，仅将所有 `CHANGE_ME_*` 替换为真实密码；候选脚本写入的 `TARGET_PLATFORM` 和五个镜像标签不得修改。将 `WEB_HEALTH_ACL_FILE` 改为 `../site/site-health-acl.conf`，使只读站点文件不会从候选默认示例加载：

| 变量 | 说明 |
|------|------|
| `POSTGRES_PASSWORD` | PostgreSQL 管理员密码（首次启动时设置） |
| `RUISHENG_GW_PASSWORD` | 网关数据库角色密码 |
| `RUISHENG_API_PASSWORD` | API 数据库角色密码 |
| `REDIS_PASSWORD` | Redis 访问密码 |
| `JWT_SECRET` | JWT 签名密钥（≥32 字符随机字符串） |

网络变量默认只绑定到回环地址，不能把模板值当作现场批准。将受控网络 Profile、TLS 终止点和防火墙/ACL 方案确认后，再把以下变量改为批准的具体宿主机地址和端口；不要使用 `0.0.0.0`、`::` 或空值作为旁路。

| 变量 | 作用 |
|------|------|
| `WEB_BIND_HOST` / `WEB_BIND_PORT` | Web 宿主机入口 |
| `GW_DEVICE_BIND_HOST` / `GW_DEVICE_BIND_PORT` | GW 设备 TCP 入口 |
| `GW_HEALTH_BIND_HOST` / `GW_HEALTH_BIND_PORT` | GW health/ready/metrics 管理入口 |
| `GW_HEALTH_HOST` | 容器内 health listener；模板为 Docker 可达的 `0.0.0.0`，宿主暴露仍由 `GW_HEALTH_BIND_HOST` 控制 |
| `GW_HEALTH_ALLOWED_CIDRS` | GW health/ready/metrics 源 CIDR，必须与 Profile 的运维/监控网段和站点 ACL 完全一致 |
| `WEB_HEALTH_ACL_FILE` | 候选外部的只读 ACL 文件，必须指向 `../site/site-health-acl.conf` |
| 站点 Compose override | 将候选目录外的 health ACL 以只读方式挂载到 `/etc/nginx/site-health-acl.conf` |

从候选目录复制 ACL 和 Profile 模板到站点目录后，只按已批准的监控 CIDR 修改 `allow` 行，并保留最后的 `deny all;`。Profile 的审批、网段、三组宿主绑定、传输模式、TLS/旁路、防火墙和探测位置字段必须全部填写；任何 `UNRESOLVED` 或默认路由都保持 BLOCKED。不要修改候选基础 Compose。

当 Web 使用非回环绑定时，TLS 字段不能只写“已配置”或复制说明文字，必须使用 validator 可解析的显式证据：`HTTPS_WSS` 至少填写 `termination=...; certificate=...; domain=...; firewall=...; direct_http=deny; direct_ws=deny`；隔离可信 HTTP 至少填写 `isolation=...; firewall=...; direct_http=trusted-only; direct_ws=trusted-only`。值应为脱敏的终止点、证书保管引用、域名和防火墙规则标识，不要写私钥。

```bash
cp site-health-acl.conf.example ../site/site-health-acl.conf
cp site-acceptance-profile.md.example ../site/site-acceptance-profile.md
```

```powershell
Copy-Item site-health-acl.conf.example ..\site\site-health-acl.conf
Copy-Item site-acceptance-profile.md.example ..\site\site-acceptance-profile.md
```

数据库和 Redis 密码只能使用 URL-safe 字符：`A-Z a-z 0-9 . _ ~ -`。启动时会拒绝占位值和其他字符，避免连接字符串被特殊字符破坏。

**生成随机密码（Linux/Mac）：**
```bash
openssl rand -hex 24
```

**生成随机密码（Windows PowerShell）：**
```powershell
-join ((1..48) | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) })
```

### 3. 首次启动

使用最终站点环境再次核对六个服务的候选标签和目标平台。校验失败时不得启动。网络 validator 必须使用基础 Compose 与只读站点 override 的实际渲染结果；禁止提供手工 rendered JSON：

```bash
bash ./verify-candidate.sh . ../site/.env.prod
```

```powershell
.\verify-candidate.ps1 . ..\site\.env.prod
```

```bash
python3 ./validate-network-boundary.py \
  --compose ./docker-compose.prod.yml \
  --compose ./site-network.override.yml \
  --env-file ../site/.env.prod \
  --profile ../site/site-acceptance-profile.md \
  --nginx-config ./nginx.conf \
  --acl-file ../site/site-health-acl.conf
```

```powershell
py -3 .\validate-network-boundary.py `
  --compose .\docker-compose.prod.yml `
  --compose .\site-network.override.yml `
  --env-file ..\site\.env.prod `
  --profile ..\site\site-acceptance-profile.md `
  --nginx-config .\nginx.conf `
  --acl-file ..\site\site-health-acl.conf
```

仅当上一个命令输出 `[network] PASS` 时，才可启动；其返回 `BLOCKED` 或 `FAIL` 都禁止 Go。

```bash
docker compose -f docker-compose.prod.yml -f site-network.override.yml --env-file ../site/.env.prod up -d postgres redis migrate
```

首次启动会自动完成：
- 创建数据库表结构（约 30 秒）

查看初始化进度：
```bash
docker compose -f docker-compose.prod.yml -f site-network.override.yml --env-file ../site/.env.prod logs migrate -f
```

看到 `Database initialised successfully.` 后，数据库结构迁移完成；API、GW 和 Web 尚未启动。

### 4. 对外开放前置条件

生产 bootstrap 不创建演示数据或账号。管理员引导和凭据交接尚未交付，B-02 不解除 G0-05/CAP-2；在独立流程获批并完成前，不得将系统开放给用户或提供 Web 访问入口。

### 5. RS485 串口设备（可选）

候选基础 Compose 禁止现场编辑。串口设备映射和 `GW_SERIAL_PORTS` 必须由另行批准、单独校验的站点 Compose override 注入；该 Profile/override 尚未在 B-03 中实现，因此当前保持 B-05 阻断。

## 日常管理

以下全栈重启和升级命令仅在管理员引导及凭据交接通过独立流程获批并完成后使用；当前交付状态不得执行。

```bash
# 每次启动、重启或回滚前先运行边界校验；非 PASS 不得执行 Compose。
python3 ./validate-network-boundary.py \
  --compose ./docker-compose.prod.yml \
  --compose ./site-network.override.yml \
  --env-file ../site/.env.prod \
  --profile ../site/site-acceptance-profile.md \
  --nginx-config ./nginx.conf \
  --acl-file ../site/site-health-acl.conf

# 停止系统
docker compose -f docker-compose.prod.yml -f site-network.override.yml --env-file ../site/.env.prod down

# 重新启动（保留数据）
docker compose -f docker-compose.prod.yml -f site-network.override.yml --env-file ../site/.env.prod up -d

# 查看所有服务日志
docker compose -f docker-compose.prod.yml -f site-network.override.yml --env-file ../site/.env.prod logs -f

# 查看指定服务日志
docker compose -f docker-compose.prod.yml -f site-network.override.yml --env-file ../site/.env.prod logs api -f

# 数据库备份
docker exec ruisheng-postgres pg_dump -U ruisheng_admin ruisheng > backup_$(date +%Y%m%d).sql
```

## 升级

先在新候选目录中完成候选校验，再只把六个发布方管理字段（`TARGET_PLATFORM` 和五个 `*_IMAGE`）从新候选模板合并到既有站点环境文件。不得用旧候选标签启动新包，也不得覆盖站点密码和运行参数。

**Linux/Mac：**

```bash
set -euo pipefail

# 校验并加载新候选
bash ./verify-candidate.sh .

# 原子更新六个发布字段，保留站点密码和运行参数
python3 - .env.prod.example ../site/.env.prod <<'PY'
import os, pathlib, shutil, sys
keys = {"TARGET_PLATFORM", "POSTGRES_IMAGE", "REDIS_IMAGE", "API_IMAGE", "GW_IMAGE", "WEB_IMAGE"}
source, target = map(pathlib.Path, sys.argv[1:])
release = {k: v for line in source.read_text(encoding="utf-8").splitlines()
           for k, sep, v in [line.partition("=")] if sep and k in keys}
lines, seen = [], set()
for line in target.read_text(encoding="utf-8").splitlines():
    key, sep, _ = line.partition("=")
    if sep and key in keys:
        line, seen = f"{key}={release[key]}", seen | {key}
    lines.append(line)
if set(release) != keys:
    raise SystemExit("release field set mismatch")
lines.extend(f"{key}={release[key]}" for key in sorted(keys - seen))
temporary = target.with_name(target.name + ".tmp")
temporary.touch(mode=0o600, exist_ok=False)
try:
    metadata = target.stat()
    os.chown(temporary, metadata.st_uid, metadata.st_gid)
    shutil.copystat(target, temporary, follow_symlinks=False)
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temporary, target)
finally:
    temporary.unlink(missing_ok=True)
PY

# 用最终站点环境再次闭环候选标签和平台，然后停止旧版本
bash ./verify-candidate.sh . ../site/.env.prod
python3 ./validate-network-boundary.py \
  --compose ./docker-compose.prod.yml \
  --compose ./site-network.override.yml \
  --env-file ../site/.env.prod \
  --profile ../site/site-acceptance-profile.md \
  --nginx-config ./nginx.conf \
  --acl-file ../site/site-health-acl.conf
docker compose -f docker-compose.prod.yml -f site-network.override.yml --env-file ../site/.env.prod down

# 重启（会自动运行新的数据库迁移）
docker compose -f docker-compose.prod.yml -f site-network.override.yml --env-file ../site/.env.prod up -d
```

**Windows PowerShell：**

```powershell
$ErrorActionPreference = "Stop"
.\verify-candidate.ps1 .
$Keys = @("TARGET_PLATFORM", "POSTGRES_IMAGE", "REDIS_IMAGE", "API_IMAGE", "GW_IMAGE", "WEB_IMAGE")
$Release = @{}
Get-Content .env.prod.example | ForEach-Object {
    $Key, $Value = $_ -split "=", 2
    if ($Keys -contains $Key) { $Release[$Key] = $Value }
}
$Site = "..\site\.env.prod"
$Seen = @{}
$Lines = Get-Content $Site | ForEach-Object {
    $Key, $Value = $_ -split "=", 2
    if ($Keys -contains $Key) { $Seen[$Key] = $true; "$Key=$($Release[$Key])" } else { $_ }
}
if ($Release.Count -ne 6) { throw "release field set mismatch" }
$Keys | Where-Object { -not $Seen.ContainsKey($_) } | ForEach-Object {
    $Lines += "$_=$($Release[$_])"
}
$SiteAcl = Get-Acl -LiteralPath $Site
$TemporarySite = "$Site.tmp"
New-Item -ItemType File -Path $TemporarySite -ErrorAction Stop | Out-Null
try {
    Set-Acl -LiteralPath $TemporarySite -AclObject $SiteAcl
    [IO.File]::WriteAllLines($TemporarySite, $Lines, [Text.UTF8Encoding]::new($false))
    Move-Item -Force $TemporarySite $Site
} finally {
    Remove-Item -LiteralPath $TemporarySite -Force -ErrorAction SilentlyContinue
}
.\verify-candidate.ps1 . $Site
py -3 .\validate-network-boundary.py `
  --compose .\docker-compose.prod.yml `
  --compose .\site-network.override.yml `
  --env-file $Site `
  --profile ..\site\site-acceptance-profile.md `
  --nginx-config .\nginx.conf `
  --acl-file ..\site\site-health-acl.conf
docker compose -f docker-compose.prod.yml -f site-network.override.yml --env-file $Site down
docker compose -f docker-compose.prod.yml -f site-network.override.yml --env-file $Site up -d
```

## 故障排查

| 现象 | 可能原因 | 解决方法 |
|------|---------|---------|
| 无法登录 | 管理员引导和凭据交接尚未交付 | 保持系统不对外开放，等待独立流程获批并完成 |
| 候选校验通过但仍显示 BLOCKED | 尚未配置批准的发布签名/可信分发 | 不得将 SHA 当作发布者身份；等待 Profile 单独批准 |
| 候选校验报告 extra/missing | 候选目录被添加、删除或编辑 | 拒绝使用，从受信来源重新取得完整候选 |
| 数据库启动失败 | 磁盘空间不足 | 清理磁盘，至少保留 5 GB |
| API 报错 | 服务未就绪 | 等待 30 秒后重试 |

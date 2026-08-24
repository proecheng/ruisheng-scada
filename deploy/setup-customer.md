# 江苏润盛 SCADA — 部署说明

## 前提条件

- Windows 10/11（PowerShell 7.3+）或 Ubuntu 20.04+
- 已安装 [Docker Desktop](https://www.docker.com/products/docker-desktop/)
  （Windows 用户安装 Docker Desktop；Linux 用户安装 Docker Engine + Docker Compose v2）
- Linux 校验脚本需要系统自带的 `python3`；Windows 需要系统级 Python Launcher
  `C:\Windows\py.exe`；两端都需要 OpenSSH `ssh-keygen -Y`
- 80、5020、9090 端口未被占用
- 内存：建议 4 GB 以上

## 部署步骤

### 1. 校验候选并加载镜像

将完整候选目录复制到目标机器，在候选目录中打开终端。不要编辑、删除或添加候选目录中的任何文件；站点密钥和配置保存在候选目录外。信任锚和外置引导校验器必须由管理员预先安装，不能从候选包复制：Windows 固定为 `C:\ProgramData\Ruisheng\trust`，Linux 固定为 `/etc/ruisheng/trust`。信任目录仅包含严格单行 `release-allowed-signers` 和 `release-key-fingerprint`，且只允许管理员/SYSTEM（Linux 为 root）写入。

只运行安装在候选外、受 ACL 保护的 bootstrap；它以固定 principal `ruisheng-release` 和 namespace `ruisheng-candidate-v1` 验证 `SHA256SUMS` 原始字节，把固定 allowlist 的完整候选复制到受保护快照并重新核对全包哈希，然后从该认证快照执行包内校验器。不要从候选目录直接启动校验器。Manifest 的 `SIGNED` 只是声明，不能单独称为 `VERIFIED`。

**Windows（PowerShell）：**
```powershell
Set-ExecutionPolicy -Scope Process Bypass
C:\ProgramData\Ruisheng\bin\verify-publisher.ps1 .
```

**Linux（Terminal）：**
```bash
sudo /usr/bin/env -i PATH=/usr/bin:/bin /bin/bash /usr/local/lib/ruisheng/verify-publisher.sh .
```

脚本严格检查外置信任锚、发布者签名、文件 allowlist、`SHA256SUMS`、五个归档、目标平台、候选标签、镜像 ID 及离线 Compose。真实性失败以退出码 `1` 结束且不会调用 Docker；脚本不会访问 registry、构建或启动服务。Linux 运行时快照固定在 `/var/lib/ruisheng/work`，Windows 固定在 `C:\ProgramData\Ruisheng\work`；这两个目录和每次运行创建的 Docker 配置均须保持管理员/root 保护，不能用临时目录、用户 Docker 配置或插件目录替代。

即使站点 ACL 和 Profile 已经提供，发布者校验器也会明确输出 B-04 `BLOCKED` 并以退出码 `2` 结束。这是预期的安全边界：真实性与离线 Compose 校验不构成现场网络验收。只能在下一步通过独立的现场验收流程关闭 B-04，不能把退出码 `2` 当作可启动信号。

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

用文本编辑器打开 `../site/.env.prod`，将密码类 `CHANGE_ME_*` 替换为真实密码，并将 `MANAGEMENT_TOKEN_SHA256` 替换为批准管理令牌的 SHA-256 摘要；候选脚本写入的 `TARGET_PLATFORM` 和五个镜像标签不得修改。将 `WEB_HEALTH_ACL_FILE` 改为 `../site/site-health-acl.conf`，使只读站点文件不会从候选默认示例加载：

| 变量 | 说明 |
|------|------|
| `POSTGRES_PASSWORD` | PostgreSQL 管理员密码（首次启动时设置） |
| `RUISHENG_GW_PASSWORD` | 网关数据库角色密码 |
| `RUISHENG_API_PASSWORD` | API 数据库角色密码 |
| `REDIS_PASSWORD` | Redis 访问密码 |
| `JWT_SECRET` | JWT 签名密钥（≥32 字符随机字符串） |
| `MANAGEMENT_TOKEN_SHA256` | 高熵管理 Bearer 令牌的 SHA-256（64 位小写十六进制）；不得填写或保存原始令牌 |

网络变量默认只绑定到回环地址，不能把模板值当作现场批准。将受控网络 Profile、TLS 终止点和防火墙/ACL 方案确认后，再把以下变量改为批准的具体宿主机地址和端口；不要使用 `0.0.0.0`、`::` 或空值作为旁路。

| 变量 | 作用 |
|------|------|
| `APP_NETWORK_SUBNET` / `APP_NETWORK_GATEWAY` | 固定 Docker 应用 bridge 及网关；必须与 Profile 和现场无冲突路由一致 |
| `WEB_BIND_HOST` / `WEB_BIND_PORT` | Web 宿主机入口 |
| `GW_DEVICE_BIND_HOST` / `GW_DEVICE_BIND_PORT` | GW 设备 TCP 入口 |
| `GW_HEALTH_BIND_HOST` / `GW_HEALTH_BIND_PORT` | GW health/ready/metrics 管理入口 |
| `GW_HEALTH_HOST` | 容器内 health listener；模板为 Docker 可达的 `0.0.0.0`，宿主暴露仍由 `GW_HEALTH_BIND_HOST` 控制 |
| `GW_HEALTH_ALLOWED_CIDRS` | GW 容器实际观察到的管理探针源，必须与 Profile 的“管理端点容器观察来源”和站点 ACL 完全一致 |
| `MANAGEMENT_TOKEN_SHA256` | 同时注入 API/GW 的不可逆摘要；普通容器、Web/Nginx 和迁移服务不得接收原始令牌或摘要 |
| `WEB_HEALTH_ACL_FILE` | 候选外部的只读 ACL 文件，必须指向 `../site/site-health-acl.conf` |
| 站点 Compose override | 将候选目录外的 health ACL 以只读方式挂载到 `/etc/nginx/site-health-acl.conf` |

从候选目录复制 ACL 和 Profile 模板到站点目录后，先确认 `APP_NETWORK_SUBNET` 不与宿主、VPN、客户 LAN 或现有 Docker 网络冲突，并记录固定 `APP_NETWORK_GATEWAY`。Windows Docker Desktop 经宿主发布端口转发时，容器通常看到的是该精确网关地址而不是原始监控地址；因此 ACL `allow`、`GW_HEALTH_ALLOWED_CIDRS` 和 Profile 的“管理端点容器观察来源”应写精确网关主机路由（IPv4 为 `/32`），不得写整个 bridge。Docker hairpin 也可能被改写成同一网关，故 API/GW 还必须验证独立 Bearer 管理凭据；原始运维/监控 CIDR仍由批准的宿主绑定、防火墙或上游入口限制，不能信任客户端可写的 `X-Forwarded-For`。保留最后的 `deny all;`。

在批准的密码管理器或监控平台中生成至少 32 随机字节的 URL-safe 管理令牌，并只向监控采集器交付原始值。站点 `.env.prod` 和 Profile 只记录其 SHA-256 摘要；Git、候选、镜像、Compose 环境、命令历史、截图和共享证据不得出现原始令牌。轮换时先更新批准 Profile 和站点摘要，用 `docker compose --env-file ../site/.env.prod -f docker-compose.prod.yml -f site-network.override.yml up -d --force-recreate api gw` 重建 API/GW，再更新监控采集器并验证新令牌成功、旧令牌返回 403、容器环境摘要已经更新；完成正反向探测后销毁旧令牌。`docker compose restart` 不会重新加载 `.env.prod`，不得用于凭据轮换。

Profile 的审批、原始网段、Docker 子网/网关、容器观察来源、三组宿主绑定、管理认证方案/摘要/责任人、传输模式、TLS/旁路、防火墙和探测位置字段必须全部填写；任何 `UNRESOLVED`、非法摘要或默认路由都保持 BLOCKED。静态 validator 的 PASS 只表示配置内部一致，不能替代四类来源、无/错凭据、监听清单、防火墙和重启后的现场正反向探测。不要修改候选基础 Compose。

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
sudo /usr/bin/env -i PATH=/usr/bin:/bin /bin/bash /usr/local/lib/ruisheng/verify-publisher.sh . ../site/.env.prod
```

```powershell
C:\ProgramData\Ruisheng\bin\verify-publisher.ps1 . ..\site\.env.prod
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

候选基础 Compose 禁止现场编辑。Windows Docker Desktop 使用 `usbipd-win` 把 USB-RS485
适配器交给 WSL；不能把 Windows `COM3` 猜测为 `/dev/ttyS2`。主机附加只证明设备可见，
不代表已获得向真实设备发送 Modbus 帧的授权。

发布者校验器会把已认证快照中的硬件工具原子安装到受保护的 `C:\Ruisheng\tools`；
不要从下载目录、项目目录或未经认证的候选目录手工复制高权限脚本。
硬件配置仍放在 `C:\Ruisheng\site`，候选本身保持只读不变。
适配器必须使用 VID、PID 和唯一 USB 序列号识别；BUSID 仅用于当前附加，不能保存为站点身份。

```powershell
C:\ProgramData\Ruisheng\bin\verify-publisher.ps1 . -InstallSerialTools
New-Item -ItemType Directory -Force C:\Ruisheng\site | Out-Null
Copy-Item .\site-serial-hardware.json.example C:\Ruisheng\site\serial-hardware.json
# 只填写已核对的适配器 vendor_id、product_id 和 serial_number；采集参数未知时保持 polling_approved=false。

C:\Ruisheng\tools\install_serial_hardware_task.ps1 `
  -ConfigPath C:\Ruisheng\site\serial-hardware.json
```

任务 `Ruisheng-Serial-Hardware-Attach` 在开机后按硬件身份重新发现设备，执行 usbipd
bind/attach，加载 WSL 的 `usbserial`/`ftdi_sio`，并创建稳定别名
`/dev/ruisheng-rs485`。设备缺席时任务重试，不得阻断现有应用容器。状态记录在
`C:\Ruisheng\audit\serial-hardware.jsonl`，不得包含密钥或设备协议数据。

在设备型号、Modbus RTU 点表、波特率、8N1、从站地址和审批人未填写前，运行校验器应返回
`[serial-hardware] BLOCKED`；此时不得添加设备映射或重建 GW：

```powershell
py -3 C:\Ruisheng\tools\validate_serial_hardware.py `
  --config C:\Ruisheng\site\serial-hardware.json
```

只有上述参数全部批准后，才复制 `site-serial.env.example` 和
`site-serial.override.yml` 到候选目录外的站点目录，填入与批准配置一致的稳定路径和波特率，
并同时渲染基础、网络和串口 override。validator 只有输出 `PASS` 才允许重建 GW：

```powershell
py -3 C:\Ruisheng\tools\validate_serial_hardware.py `
  --config C:\Ruisheng\site\serial-hardware.json `
  --candidate-root (Get-Location).Path `
  --compose .\docker-compose.prod.yml `
  --compose .\site-network.override.yml `
  --serial-override C:\Ruisheng\site\site-serial.override.yml `
  --env-file ..\site\.env.prod `
  --serial-env-file C:\Ruisheng\site\site-serial.env `
  --hardware-attestation C:\Ruisheng\audit\serial-hardware-state.json

docker compose --env-file ..\site\.env.prod `
  -f .\docker-compose.prod.yml `
  -f .\site-network.override.yml `
  -f C:\Ruisheng\site\site-serial.override.yml `
  --env-file C:\Ruisheng\site\site-serial.env `
  up -d --force-recreate gw
```

validator 会在重建 GW 前通过只读数据库查询核对 `dev_number`、
`transport_type='serial'`、`serial_port`、`baud_rate`、`modbus_addr` 和 `is_enabled=true`。
缺少五分钟内的 ready 硬件状态或数据库记录不一致时只返回 `FAIL`，不得重建 GW。
真实控制、越限告警和通知测试另行授权。

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
sudo /usr/bin/env -i PATH=/usr/bin:/bin /bin/bash /usr/local/lib/ruisheng/verify-publisher.sh .

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
sudo /usr/bin/env -i PATH=/usr/bin:/bin /bin/bash /usr/local/lib/ruisheng/verify-publisher.sh . ../site/.env.prod
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
C:\ProgramData\Ruisheng\bin\verify-publisher.ps1 .
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
C:\ProgramData\Ruisheng\bin\verify-publisher.ps1 . $Site
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
| 发布者真实性失败 | 锚、指纹、principal/namespace、签名或任一候选字节不匹配 | 禁止加载镜像，从批准渠道重新取得锚或完整候选；不得从候选内替换公钥 |
| 候选校验报告 extra/missing | 候选目录被添加、删除或编辑 | 拒绝使用，从受信来源重新取得完整候选 |
| 数据库启动失败 | 磁盘空间不足 | 清理磁盘，至少保留 5 GB |
| API 报错 | 服务未就绪 | 等待 30 秒后重试 |

## 发布人员：签名候选

专用 Ed25519 私钥只保存在发布机当前用户目录，必须使用随机口令加密。口令只允许由当前用户 DPAPI 密文或交互式 `ssh-agent` 解锁，不得出现在命令参数、环境变量、Git、候选目录或日志中。`release-allowed-signers`、`release-key-fingerprint` 和签名身份必须在构建前由发布负责人核对；轮换密钥、principal 或 namespace 需要另行批准。

候选发布根是安全边界，必须在构建前由管理员创建并限制为发布账户、Administrators 和 SYSTEM（Linux 为发布账户/root）可写；生成器会拒绝继承 `Authenticated Users`、普通用户或其他主体写权限的目录。不要使用项目目录下的 `dist/deploy`、下载目录或临时目录。示例受保护发布根：Windows `C:\ProgramData\Ruisheng\publisher-output`，Linux `$HOME/.local/share/ruisheng/publisher-output`（目录及其祖先不可被组或其他用户写入）。

```bash
RELEASE_OUTPUT_ROOT=/protected/release/output /bin/bash deploy/export-images.sh deploy-YYYYMMDD.N linux/amd64 \
  /protected/release/ruisheng-release.pub /protected/release/trust
```

发布入口只接受与批准锚完全一致的 `ruisheng-release.pub`，并要求对应加密私钥已加载到 `ssh-agent`；私钥路径会被拒绝，从而避免脚本接触口令或未加密私钥。生成器对 `SHA256SUMS` 原始字节签名，立即使用批准的包外锚回验，失败时删除 staging、构建锁和本次候选标签；成功候选只包含 `SHA256SUMS.sig`，不包含公钥、私钥或 DPAPI 密文。

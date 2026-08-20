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

脚本严格检查文件 allowlist、`SHA256SUMS`、五个归档、目标平台、候选标签、镜像 ID 及离线 Compose。任何缺失、额外、重复、路径越界、篡改或身份漂移都会在启动前失败；脚本不会访问 registry、构建或启动服务。

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

用文本编辑器打开 `../site/.env.prod`，仅将所有 `CHANGE_ME_*` 替换为真实密码；候选脚本写入的 `TARGET_PLATFORM` 和五个镜像标签不得修改：

| 变量 | 说明 |
|------|------|
| `POSTGRES_PASSWORD` | PostgreSQL 管理员密码（首次启动时设置） |
| `RUISHENG_GW_PASSWORD` | 网关数据库角色密码 |
| `RUISHENG_API_PASSWORD` | API 数据库角色密码 |
| `REDIS_PASSWORD` | Redis 访问密码 |
| `JWT_SECRET` | JWT 签名密钥（≥32 字符随机字符串） |

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

使用最终站点环境再次核对六个服务的候选标签和目标平台。校验失败时不得启动：

```bash
bash ./verify-candidate.sh . ../site/.env.prod
```

```powershell
.\verify-candidate.ps1 . ..\site\.env.prod
```

```bash
docker compose -f docker-compose.prod.yml --env-file ../site/.env.prod up -d postgres redis migrate
```

首次启动会自动完成：
- 创建数据库表结构（约 30 秒）

查看初始化进度：
```bash
docker compose -f docker-compose.prod.yml --env-file ../site/.env.prod logs migrate -f
```

看到 `Database initialised successfully.` 后，数据库结构迁移完成；API、GW 和 Web 尚未启动。

### 4. 对外开放前置条件

生产 bootstrap 不创建演示数据或账号。管理员引导和凭据交接尚未交付，B-02 不解除 G0-05/CAP-2；在独立流程获批并完成前，不得将系统开放给用户或提供 Web 访问入口。

### 5. RS485 串口设备（可选）

候选基础 Compose 禁止现场编辑。串口设备映射和 `GW_SERIAL_PORTS` 必须由另行批准、单独校验的站点 Compose override 注入；该 Profile/override 尚未在 B-03 中实现，因此当前保持 B-05 阻断。

## 日常管理

以下全栈重启和升级命令仅在管理员引导及凭据交接通过独立流程获批并完成后使用；当前交付状态不得执行。

```bash
# 停止系统
docker compose -f docker-compose.prod.yml --env-file ../site/.env.prod down

# 重新启动（保留数据）
docker compose -f docker-compose.prod.yml --env-file ../site/.env.prod up -d

# 查看所有服务日志
docker compose -f docker-compose.prod.yml --env-file ../site/.env.prod logs -f

# 查看指定服务日志
docker compose -f docker-compose.prod.yml --env-file ../site/.env.prod logs api -f

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
docker compose -f docker-compose.prod.yml --env-file ../site/.env.prod down

# 重启（会自动运行新的数据库迁移）
docker compose -f docker-compose.prod.yml --env-file ../site/.env.prod up -d
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
docker compose -f docker-compose.prod.yml --env-file $Site down
docker compose -f docker-compose.prod.yml --env-file $Site up -d
```

## 故障排查

| 现象 | 可能原因 | 解决方法 |
|------|---------|---------|
| 无法登录 | 管理员引导和凭据交接尚未交付 | 保持系统不对外开放，等待独立流程获批并完成 |
| 候选校验通过但仍显示 BLOCKED | 尚未配置批准的发布签名/可信分发 | 不得将 SHA 当作发布者身份；等待 Profile 单独批准 |
| 候选校验报告 extra/missing | 候选目录被添加、删除或编辑 | 拒绝使用，从受信来源重新取得完整候选 |
| 数据库启动失败 | 磁盘空间不足 | 清理磁盘，至少保留 5 GB |
| API 报错 | 服务未就绪 | 等待 30 秒后重试 |

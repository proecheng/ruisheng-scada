# 江苏润盛 SCADA — Windows Server 原生部署指南

适用于 **Windows Server 2019 / 2022**，不依赖 Docker。
所有组件以 Windows 服务形式运行，随系统自动启动。

---

## 目录结构

```
D:\ruisheng\
├── api\              # ruisheng-api 代码 + .venv
├── gw\               # ruisheng-gw 代码 + .venv
│   └── wal\          # WAL 落盘目录（自动创建）
├── web\dist\         # Vue 前端静态文件
├── nginx\            # Nginx 程序 + 配置
├── logs\
│   ├── api\
│   ├── gw\
│   └── nginx\
├── backup\           # 数据库备份
└── config\
    ├── ruisheng-api.env   # API 运行时密钥（勿入 git）
    └── ruisheng-gw.env    # GW 运行时密钥（勿入 git）
```

---

## §1 前提条件

- 管理员账户
- 网络连通（或已准备离线安装包）
- 开放端口：80（HTTP）、5020（DTU TCP）、5432（PG 仅内网）、6379（Redis 仅内网）

安装完成后开放防火墙入站规则（以管理员身份运行）：

```powershell
# HTTP（前端访问）
New-NetFirewallRule -DisplayName "Ruisheng HTTP" -Direction Inbound `
    -Protocol TCP -LocalPort 80 -Action Allow

# DTU TCP 接入（RS485 网关设备连接）
New-NetFirewallRule -DisplayName "Ruisheng GW DTU" -Direction Inbound `
    -Protocol TCP -LocalPort 5020 -Action Allow
```

> 5432（PostgreSQL）和 6379（Redis）**不应对外开放**，仅限本机内部通信。

---

## §2 安装前置组件

以管理员身份打开 PowerShell，执行：

```powershell
Set-ExecutionPolicy RemoteSigned -Scope Process
.\install.ps1
```

脚本将自动安装：

| 组件 | 版本 | 说明 |
|------|------|------|
| PostgreSQL | 15.x | 主数据库 |
| TimescaleDB | 2.16.x | 时序数据扩展（**需手动安装，见下**） |
| Memurai | 最新 | Redis 兼容，Windows 原生 |
| Python | 3.11.x | 运行时 |
| uv | 最新 | Python 包管理 |
| NSSM | 2.24 | 进程→Windows 服务 |
| Nginx | 1.27.x | 反向代理 + 静态文件 |

### TimescaleDB 手动安装步骤

1. 下载：https://docs.timescale.com/self-hosted/latest/install/installation-windows/
   选择 **PostgreSQL 15** 对应版本。
2. 运行安装包，按提示完成。
3. 编辑 `postgresql.conf`（默认在 `C:\Program Files\PostgreSQL\15\data\`）：
   ```
   shared_preload_libraries = 'timescaledb'
   ```
4. 在 services.msc 中重启 `postgresql-x64-15` 服务。

---

## §3 初始化 PostgreSQL

以 `postgres` 超级用户连接（pgAdmin 或 psql），依次执行：

```sql
-- 建库
CREATE DATABASE ruisheng;

-- 建角色（密码须与 .env 文件保持一致）
-- ruisheng_admin：仅需 CREATEDB + CREATEROLE，用于迁移和备份
CREATE ROLE ruisheng_admin  LOGIN PASSWORD 'CHANGE_ME_ADMIN_PW'  CREATEDB CREATEROLE;
CREATE ROLE ruisheng_api    LOGIN PASSWORD 'CHANGE_ME_API_PW';
-- ruisheng_gw 须有 BYPASSRLS，登录时需绕过 RLS 查询 users 表
CREATE ROLE ruisheng_gw     LOGIN PASSWORD 'CHANGE_ME_GW_PW'     BYPASSRLS;

-- 以 postgres 超级用户连接 ruisheng 库启用扩展（ruisheng_admin 权限不足以创建扩展）
\c ruisheng
CREATE EXTENSION IF NOT EXISTS timescaledb;
```

> **密码生成（PowerShell）** — 生成 24 位字母+数字密码：
> ```powershell
> -join ((65..90)+(97..122)+(48..57) | Get-Random -Count 24 | %{[char]$_})
> ```
> 注意：env 文件中密码值**不要加引号**，否则引号会被作为密码的一部分传入。

---

## §4 配置 Memurai（Redis）

Memurai 安装后默认以 Windows 服务运行（服务名 `Memurai`）。

设置密码（与 .env 文件保持一致）：

```powershell
# 方法一：命令行（需要 memurai-cli 在 PATH 中）
memurai-cli CONFIG SET requirepass "CHANGE_ME_REDIS_PW"
memurai-cli CONFIG REWRITE

# 方法二：编辑 Memurai 配置文件
# 默认路径：C:\Program Files\Memurai\memurai.conf
# 找到 # requirepass foobared 行，改为：
# requirepass CHANGE_ME_REDIS_PW
# 然后在 services.msc 重启 Memurai 服务
```

验证：

```powershell
memurai-cli -a CHANGE_ME_REDIS_PW ping
# 返回 PONG 即表示正常
```

---

## §5 部署代码

### 5.1 复制文件

将源码仓库根目录（monorepo 根）完整复制到 `D:\ruisheng\src\`，然后创建软链接或按以下结构组织：

```
D:\ruisheng\src\           ← 仓库根（含 pyproject.toml、uv.lock、alembic/ 等）
├── ruisheng-api\
├── ruisheng-gw\
├── ruisheng-shared\
├── alembic\
├── alembic.ini
├── seeds\
├── tools\
├── scripts\
├── pyproject.toml
└── uv.lock
```

同时复制：

| 来源（构建机） | 目标（客户机） |
|--------------|--------------|
| `ruisheng-web/dist/`（已构建） | `D:\ruisheng\web\dist\` |
| `deploy\windows\nginx-windows.conf` | `D:\ruisheng\nginx\conf\nginx.conf`（覆盖原文件） |

> **说明**：`uv sync` 和 `alembic` 必须在含 `pyproject.toml` 和 `uv.lock` 的 workspace 根目录执行，因此将整个仓库根复制到 `D:\ruisheng\src\` 是最简单可靠的做法。

### 5.2 安装 Python 依赖

```powershell
# 在 workspace 根目录执行（含 pyproject.toml + uv.lock）
cd D:\ruisheng\src

# 安装 ruisheng-api 依赖
uv sync --package ruisheng-api --no-dev --frozen

# 安装 ruisheng-gw 依赖（含串口支持）
uv sync --package ruisheng-gw --no-dev --frozen --extra serial
```

> uv 会在 `D:\ruisheng\src\.venv\` 创建共享虚拟环境，api 和 gw 均使用同一 venv。

### 5.3 配置环境变量文件

```powershell
# 复制模板
Copy-Item deploy\windows\ruisheng-api.env.example D:\ruisheng\config\ruisheng-api.env
Copy-Item deploy\windows\ruisheng-gw.env.example  D:\ruisheng\config\ruisheng-gw.env

# 用文本编辑器打开并替换所有 CHANGE_ME 为真实值
notepad D:\ruisheng\config\ruisheng-api.env
notepad D:\ruisheng\config\ruisheng-gw.env

# 限制文件权限（仅管理员可读）
icacls D:\ruisheng\config /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"
```

---

## §6 初始化数据库

```powershell
cd D:\ruisheng\src

# 加载环境变量（临时，值不要加引号）
Get-Content D:\ruisheng\config\ruisheng-api.env |
  Where-Object { $_ -notmatch "^\s*#" -and $_ -match "^\s*[A-Z][A-Z0-9_]*=.*" } |
  ForEach-Object {
    $kv = $_ -split "=", 2
    [System.Environment]::SetEnvironmentVariable($kv[0], $kv[1], "Process")
  }

# 运行数据库迁移（建表、建索引、建 RLS 策略等）
uv run alembic upgrade head

# 写入初始数据（演示设备、默认账号）
uv run python tools\run_seeds.py
```

> 看到 `Database initialised successfully.` 表示初始化完成。

---

## §7 注册 Windows 服务

```powershell
Set-ExecutionPolicy RemoteSigned -Scope Process
.\register-services.ps1
```

脚本注册以下三个服务（均设为自动启动）：

| 服务名 | 程序 | 依赖 |
|--------|------|------|
| `ruisheng-gw` | `python -m ruisheng_gw` | postgresql-x64-15, Memurai |
| `ruisheng-api` | `python -m ruisheng_api` | postgresql-x64-15, Memurai, ruisheng-gw |
| `ruisheng-nginx` | `nginx.exe` | ruisheng-api |

手动启动（首次）：

```powershell
nssm start ruisheng-gw
nssm start ruisheng-api
nssm start ruisheng-nginx
```

---

## §8 验证

```powershell
# 查看各服务状态
nssm status ruisheng-gw
nssm status ruisheng-api
nssm status ruisheng-nginx

# 测试 API 是否响应
Invoke-WebRequest http://127.0.0.1:8000/api/v1/health -UseBasicParsing

# 测试 GW health 端点
Invoke-WebRequest http://127.0.0.1:9090/health -UseBasicParsing
```

浏览器访问 `http://localhost`（或 `http://<本机IP>`）。

**默认账号：**

| 账号 | 密码 | 权限 |
|------|------|------|
| `13800138000` | `Admin@2026!` | 管理员 |
| `13800138001` | `Admin@2026!` | 普通用户 |

> **首次登录后请立即修改密码！**

---

## §9 RS485 串口设备（可选）

在 `D:\ruisheng\config\ruisheng-gw.env` 中添加：

```env
GW_SERIAL_PORTS=[{"port":"COM3","baud_rate":9600}]
```

修改后重启服务：

```powershell
nssm restart ruisheng-gw
```

查看串口日志确认是否正常扫描设备：

```powershell
Get-Content D:\ruisheng\logs\gw\service.log -Tail 50 -Wait
```

---

## §10 日常管理

```powershell
# 停止所有服务
nssm stop ruisheng-nginx
nssm stop ruisheng-api
nssm stop ruisheng-gw

# 启动所有服务
nssm start ruisheng-gw
nssm start ruisheng-api
nssm start ruisheng-nginx

# 查看实时日志
Get-Content D:\ruisheng\logs\api\service.log   -Tail 100 -Wait
Get-Content D:\ruisheng\logs\gw\service.log    -Tail 100 -Wait
Get-Content D:\ruisheng\logs\nginx\service.log -Tail 100 -Wait

# 数据库备份
$date = Get-Date -Format "yyyyMMdd"
& "C:\Program Files\PostgreSQL\15\bin\pg_dump.exe" `
    -h 127.0.0.1 -U ruisheng_admin -F c -f "D:\ruisheng\backup\ruisheng_$date.dump" ruisheng
```

---

## §11 升级

```powershell
# 1. 停止服务
nssm stop ruisheng-nginx
nssm stop ruisheng-api
nssm stop ruisheng-gw

# 2. 替换代码文件（覆盖 D:\ruisheng\src\ 整个目录）

# 3. 更新依赖
cd D:\ruisheng\src
uv sync --package ruisheng-api --no-dev --frozen
uv sync --package ruisheng-gw  --no-dev --frozen --extra serial

# 4. 运行新迁移（重新加载环境变量，同 §6）
uv run alembic upgrade head

# 5. 重启服务
nssm start ruisheng-gw
nssm start ruisheng-api
nssm start ruisheng-nginx
```

---

## §12 故障排查

| 现象 | 检查点 | 解决方法 |
|------|--------|---------|
| 网页打不开 | `nssm status ruisheng-nginx` | 检查 80 端口占用：`netstat -ano \| findstr :80` |
| API 返回 500 | `D:\ruisheng\logs\api\service.log` | 确认 DB/Redis 连接串正确 |
| GW 服务反复重启 | `D:\ruisheng\logs\gw\service.log` | 检查 DB alembic 版本；确认 `GW_WAL_DIR` 目录存在 |
| 登录失败 | PG 日志 | 检查 `ruisheng_gw` 角色是否有 `BYPASSRLS` 权限 |
| Redis 连接拒绝 | `nssm status Memurai` | 检查密码与 env 文件是否一致 |
| TimescaleDB 未激活 | PG 日志报 extension 错误 | 检查 `shared_preload_libraries` 并重启 PG |

#Requires -RunAsAdministrator
<#
.SYNOPSIS
    江苏润盛 SCADA — Windows Server 原生部署前置组件安装脚本
.DESCRIPTION
    安装 PostgreSQL 15、TimescaleDB、Memurai、Python 3.11、uv、NSSM、Nginx。
    适用于 Windows Server 2019/2022。
    运行前请确保已接入互联网（或已手动放置离线安装包至 $OfflineDir）。
.NOTES
    以管理员身份在 PowerShell 中运行：
    Set-ExecutionPolicy RemoteSigned -Scope Process
    .\install.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── 可配置参数 ──────────────────────────────────────────────────────────────
$InstallRoot  = "D:\ruisheng"
$NginxVersion = "1.27.4"
$NssmUrl      = "https://nssm.cc/release/nssm-2.24.zip"
$NginxUrl     = "https://nginx.org/download/nginx-$NginxVersion.zip"
# ────────────────────────────────────────────────────────────────────────────

function Write-Step([string]$msg) {
    Write-Host "`n==> $msg" -ForegroundColor Cyan
}

function Assert-Command([string]$cmd) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        throw "找不到命令 '$cmd'，请检查安装是否成功。"
    }
}

# ── Step 0：创建目录结构 ──────────────────────────────────────────────────
Write-Step "创建目录结构 $InstallRoot"
@(
    "$InstallRoot\src",
    "$InstallRoot\web\dist",
    "$InstallRoot\nginx\conf",
    "$InstallRoot\nginx\logs",
    "$InstallRoot\nginx\temp",
    "$InstallRoot\logs\api",
    "$InstallRoot\logs\gw",
    "$InstallRoot\logs\nginx",
    "$InstallRoot\backup",
    "$InstallRoot\config"
) | ForEach-Object { New-Item -ItemType Directory -Force -Path $_ | Out-Null }
Write-Host "  目录创建完成。"

# ── Step 1：检查 winget ───────────────────────────────────────────────────
Write-Step "检查 winget 可用性"
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw @"
未找到 winget。
  Windows Server 2022：从 https://aka.ms/getwinget 下载 App Installer 并安装。
  Windows Server 2019：winget 不内置，建议手动安装各组件或使用 Chocolatey。
"@
}
Write-Host "  winget 可用。"

# ── Step 2：PostgreSQL 15 ────────────────────────────────────────────────
Write-Step "安装 PostgreSQL 15"
winget install --id PostgreSQL.PostgreSQL.15 --silent --accept-package-agreements --accept-source-agreements
Write-Host "  PostgreSQL 15 安装完成。"
Write-Host "  ⚠  请手动完成：" -ForegroundColor Yellow
Write-Host "     1. 在 pgAdmin 中以 postgres 超级用户登录"
Write-Host "     2. CREATE DATABASE ruisheng;"
Write-Host "     3. 创建角色（见 setup-guide.md §3）"

# ── Step 3：TimescaleDB ──────────────────────────────────────────────────
Write-Step "提示：安装 TimescaleDB"
Write-Host "  TimescaleDB Windows 版须手动安装：" -ForegroundColor Yellow
Write-Host "  1. 下载：https://docs.timescale.com/self-hosted/latest/install/installation-windows/"
Write-Host "  2. 选择与 PostgreSQL 15 对应的版本"
Write-Host "  3. 安装后在 postgresql.conf 加入：shared_preload_libraries = 'timescaledb'"
Write-Host "  4. 重启 PostgreSQL 服务，然后在 ruisheng 库执行：CREATE EXTENSION IF NOT EXISTS timescaledb;"

# ── Step 4：Memurai（Redis 兼容）────────────────────────────────────────
Write-Step "安装 Memurai（Redis 兼容，Windows 原生）"
winget install --id Memurai.Memurai --silent --accept-package-agreements --accept-source-agreements
Write-Host "  Memurai 安装完成。"
Write-Host "  ⚠  请手动设置密码（见 setup-guide.md §4）" -ForegroundColor Yellow

# ── Step 5：Python 3.11 ──────────────────────────────────────────────────
Write-Step "安装 Python 3.11"
winget install --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
# 刷新 PATH
$env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("PATH", "User")
Assert-Command python
Write-Host "  Python $(python --version) 安装完成。"

# ── Step 6：uv ──────────────────────────────────────────────────────────
Write-Step "安装 uv（Python 包管理器）"
winget install --id astral-sh.uv --silent --accept-package-agreements --accept-source-agreements
# 刷新 PATH（winget 安装后需要）
$env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("PATH", "User")
Assert-Command uv
Write-Host "  uv $(uv --version) 安装完成。"

# ── Step 7：NSSM ─────────────────────────────────────────────────────────
Write-Step "安装 NSSM（Windows 服务管理工具）"
$nssmZip  = "$env:TEMP\nssm.zip"
$nssmDir  = "$env:TEMP\nssm"
Invoke-WebRequest -Uri $NssmUrl -OutFile $nssmZip
Expand-Archive -Path $nssmZip -DestinationPath $nssmDir -Force
$nssmExe = Get-ChildItem -Recurse -Filter "nssm.exe" $nssmDir |
           Where-Object { $_.Directory.Name -eq "win64" } |
           Select-Object -First 1
Copy-Item $nssmExe.FullName "C:\Windows\System32\nssm.exe" -Force
Assert-Command nssm
Write-Host "  NSSM 安装完成（C:\Windows\System32\nssm.exe）。"

# ── Step 8：Nginx for Windows ────────────────────────────────────────────
Write-Step "安装 Nginx $NginxVersion"
$nginxZip = "$env:TEMP\nginx.zip"
$nginxTmp = "$env:TEMP\nginx-extract"
Invoke-WebRequest -Uri $NginxUrl -OutFile $nginxZip
Expand-Archive -Path $nginxZip -DestinationPath $nginxTmp -Force
$nginxSrc = Get-ChildItem $nginxTmp -Directory | Select-Object -First 1
Copy-Item "$($nginxSrc.FullName)\*" "$InstallRoot\nginx\" -Recurse -Force
Write-Host "  Nginx 解压至 $InstallRoot\nginx\"

# ── 完成 ─────────────────────────────────────────────────────────────────
Write-Host @"

╔══════════════════════════════════════════════════════╗
║  前置组件安装完成                                    ║
║  请继续执行 setup-guide.md 中的后续步骤：            ║
║    §3  初始化 PostgreSQL（建库、建角色）              ║
║    §4  配置 Memurai 密码                             ║
║    §5  部署代码                                      ║
║    §6  初始化数据库（alembic + seeds）               ║
║    §7  运行 register-services.ps1 注册 Windows 服务  ║
╚══════════════════════════════════════════════════════╝
"@ -ForegroundColor Green

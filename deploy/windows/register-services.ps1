#Requires -RunAsAdministrator
<#
.SYNOPSIS
    江苏润盛 SCADA — 注册 Windows 服务（NSSM）
.DESCRIPTION
    将 ruisheng-api、ruisheng-gw、ruisheng-nginx 注册为 Windows 自动启动服务。
    运行前请确认：
      1. install.ps1 已成功执行
      2. D:\ruisheng\config\ruisheng-api.env 和 ruisheng-gw.env 已填写完毕
      3. uv sync 已在 D:\ruisheng\src\ 执行完毕
      4. alembic upgrade head 和 run_seeds.py 已完成
.NOTES
    重新注册（更新配置）时先运行：
      nssm stop  ruisheng-api; nssm remove ruisheng-api confirm
      nssm stop  ruisheng-gw;  nssm remove ruisheng-gw  confirm
      nssm stop  ruisheng-nginx; nssm remove ruisheng-nginx confirm
    然后重新运行本脚本。
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$InstallRoot = "D:\ruisheng"
$SrcRoot     = "$InstallRoot\src"   # workspace 根（pyproject.toml + uv.lock 所在）
$ConfigDir   = "$InstallRoot\config"

function Write-Step([string]$msg) {
    Write-Host "`n==> $msg" -ForegroundColor Cyan
}

function Register-NssmService {
    param(
        [string]$Name,
        [string]$Exe,
        [string]$Args,
        [string]$WorkDir,
        [string]$EnvFile,
        [string]$LogFile,
        [string[]]$DependsOn = @()
    )

    Write-Host "  注册服务：$Name"

    # 若已存在则先移除（用 Get-Service 查询，比 nssm status 退出码更可靠）
    if (Get-Service -Name $Name -ErrorAction SilentlyContinue) {
        Write-Host "    服务已存在，先停止并移除..."
        nssm stop   $Name 2>&1 | Out-Null
        nssm remove $Name confirm 2>&1 | Out-Null
        Start-Sleep -Seconds 2  # 等待 SCM 完成注销
    }

    nssm install $Name $Exe
    nssm set $Name AppParameters    $Args
    nssm set $Name AppDirectory     $WorkDir
    nssm set $Name AppStdout        $LogFile
    nssm set $Name AppStderr        $LogFile
    nssm set $Name AppRotateFiles   1
    nssm set $Name AppRotateSeconds 86400
    nssm set $Name AppRotateBytes   10485760
    nssm set $Name Start            SERVICE_AUTO_START
    nssm set $Name ObjectName       LocalSystem

    # 从 .env 文件读取环境变量注入服务
    # 过滤规则：跳过注释行（# 开头）和空行；允许值为空（KEY= 合法）
    if (Test-Path $EnvFile) {
        $envLines = Get-Content $EnvFile |
            Where-Object { $_ -notmatch "^\s*#" -and $_ -match "^\s*[A-Z][A-Z0-9_]*=.*" }
        $envBlock = $envLines -join "`n"
        nssm set $Name AppEnvironmentExtra $envBlock
    } else {
        Write-Warning "    未找到 env 文件：$EnvFile — 服务将在无环境变量的情况下启动！"
    }

    # 服务依赖
    if ($DependsOn.Count -gt 0) {
        nssm set $Name DependOnService ($DependsOn -join " ")
    }

    Write-Host "    ✓ $Name 注册成功"
}

# ── 检查前置条件 ──────────────────────────────────────────────────────────
Write-Step "检查前置条件"
@(
    "$SrcRoot\.venv\Scripts\python.exe",
    "$InstallRoot\nginx\nginx.exe",
    "$ConfigDir\ruisheng-api.env",
    "$ConfigDir\ruisheng-gw.env"
) | ForEach-Object {
    if (-not (Test-Path $_)) {
        throw "缺少文件：$_`n请确认部署步骤已完成（见 setup-guide.md）。"
    }
}
Write-Host "  前置条件检查通过。"

# ── ruisheng-gw ───────────────────────────────────────────────────────────
Write-Step "注册 ruisheng-gw（采集网关）"
Register-NssmService `
    -Name      "ruisheng-gw" `
    -Exe       "$SrcRoot\.venv\Scripts\python.exe" `
    -Args      "-m ruisheng_gw" `
    -WorkDir   "$SrcRoot" `
    -EnvFile   "$ConfigDir\ruisheng-gw.env" `
    -LogFile   "$InstallRoot\logs\gw\service.log" `
    -DependsOn @("postgresql-x64-15", "Memurai")

# ── ruisheng-api ──────────────────────────────────────────────────────────
Write-Step "注册 ruisheng-api（Web API）"
Register-NssmService `
    -Name      "ruisheng-api" `
    -Exe       "$SrcRoot\.venv\Scripts\python.exe" `
    -Args      "-m ruisheng_api" `
    -WorkDir   "$SrcRoot" `
    -EnvFile   "$ConfigDir\ruisheng-api.env" `
    -LogFile   "$InstallRoot\logs\api\service.log" `
    -DependsOn @("postgresql-x64-15", "Memurai", "ruisheng-gw")

# ── ruisheng-nginx ────────────────────────────────────────────────────────
Write-Step "注册 ruisheng-nginx（Web 前端）"
if (Get-Service -Name "ruisheng-nginx" -ErrorAction SilentlyContinue) {
    nssm stop   ruisheng-nginx 2>&1 | Out-Null
    nssm remove ruisheng-nginx confirm 2>&1 | Out-Null
    Start-Sleep -Seconds 2
}
nssm install ruisheng-nginx    "$InstallRoot\nginx\nginx.exe"
nssm set ruisheng-nginx AppDirectory    "$InstallRoot\nginx"
nssm set ruisheng-nginx AppStdout       "$InstallRoot\logs\nginx\service.log"
nssm set ruisheng-nginx AppStderr       "$InstallRoot\logs\nginx\service.log"
nssm set ruisheng-nginx AppRotateFiles  1
nssm set ruisheng-nginx AppRotateSeconds 86400
nssm set ruisheng-nginx AppRotateBytes  10485760
nssm set ruisheng-nginx Start           SERVICE_AUTO_START
nssm set ruisheng-nginx DependOnService "ruisheng-api"
Write-Host "    ✓ ruisheng-nginx 注册成功"

# ── 完成 ─────────────────────────────────────────────────────────────────
Write-Host @"

╔══════════════════════════════════════════════════════╗
║  Windows 服务注册完成                                ║
║                                                      ║
║  启动顺序（在 services.msc 中）：                    ║
║    1. postgresql-x64-15（已自动启动）                ║
║    2. Memurai         （已自动启动）                 ║
║    3. ruisheng-gw     → nssm start ruisheng-gw       ║
║    4. ruisheng-api    → nssm start ruisheng-api      ║
║    5. ruisheng-nginx  → nssm start ruisheng-nginx    ║
║                                                      ║
║  验证：浏览器访问 http://localhost                   ║
╚══════════════════════════════════════════════════════╝
"@ -ForegroundColor Green

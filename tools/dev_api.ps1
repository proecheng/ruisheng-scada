param(
  [switch]$Migrate,
  [switch]$Seed,
  [switch]$NoStart
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

$pathSeparator = [System.IO.Path]::PathSeparator
$env:PYTHONPATH = @(
  $Root,
  (Join-Path $Root "ruisheng-shared/src"),
  (Join-Path $Root "ruisheng-api/src"),
  (Join-Path $Root "ruisheng-gw/src"),
  (Join-Path $Root "tools/pcap_gen/src")
) -join $pathSeparator

$knownApiEnv = @(
  "API_LISTEN_HOST",
  "API_LISTEN_PORT",
  "API_DB_URL",
  "API_GW_DB_URL",
  "API_REDIS_URL",
  "API_JWT_SECRET",
  "API_JWT_ACCESS_TTL_SEC",
  "API_JWT_REFRESH_TTL_SEC",
  "API_OTP_TTL_SEC",
  "API_DB_POOL_SIZE",
  "API_DB_POOL_OVERFLOW",
  "API_LOGIN_FAIL_USER_MAX",
  "API_LOGIN_FAIL_USER_WINDOW_SEC",
  "API_LOGIN_LOCK_TTL_SEC",
  "API_LOGIN_FAIL_IP_MAX",
  "API_IP_BLOCK_TTL_SEC",
  "API_SLOWAPI_RATE_DEFAULT",
  "API_SLOWAPI_RATE_LOGIN",
  "API_DEFAULT_USR_GROUP",
  "API_WECHAT_API_V3_KEY",
  "API_ENV"
)

Get-ChildItem Env:API_* | Where-Object { $knownApiEnv -notcontains $_.Name } | Remove-Item

if (-not $env:RUISHENG_GW_PASSWORD) { $env:RUISHENG_GW_PASSWORD = "dev-gw-change-me" }
if (-not $env:RUISHENG_API_PASSWORD) { $env:RUISHENG_API_PASSWORD = "dev-api-change-me" }
if (-not $env:DATABASE_URL) {
  $env:DATABASE_URL = "postgresql+asyncpg://ruisheng_dev:ruisheng_dev@127.0.0.1:5432/ruisheng"
}
if (-not $env:API_DB_URL) {
  $env:API_DB_URL = "postgresql+asyncpg://ruisheng_api:$($env:RUISHENG_API_PASSWORD)@127.0.0.1:5432/ruisheng"
}
if (-not $env:API_GW_DB_URL) {
  $env:API_GW_DB_URL = "postgresql+asyncpg://ruisheng_gw:$($env:RUISHENG_GW_PASSWORD)@127.0.0.1:5432/ruisheng"
}
if (-not $env:API_REDIS_URL) { $env:API_REDIS_URL = "redis://:dev-redis-pw@127.0.0.1:6379/0" }
if (-not $env:API_JWT_SECRET) {
  $env:API_JWT_SECRET = "dev-jwt-secret-change-me-0123456789abcdef0123456789abcdef"
}
if (-not $env:API_ENV) { $env:API_ENV = "dev" }
if (-not $env:API_LISTEN_HOST) { $env:API_LISTEN_HOST = "127.0.0.1" }
if (-not $env:API_LISTEN_PORT) { $env:API_LISTEN_PORT = "8000" }

if ($Migrate) {
  uv run alembic upgrade head
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if ($Seed) {
  uv run python tools/run_seeds.py
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if ($NoStart) { exit 0 }

uv run python -m ruisheng_api

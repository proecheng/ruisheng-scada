param(
  [string]$ApiBase = "http://127.0.0.1:8000/api",
  [string]$HostAddress = "127.0.0.1",
  [int]$Port = 5173
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$WebRoot = Join-Path $Root "ruisheng-web"

Set-Location $WebRoot
if (-not $env:VITE_API_BASE) { $env:VITE_API_BASE = $ApiBase }

pnpm dev --host $HostAddress --port $Port

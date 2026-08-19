# 江苏润盛 SCADA — 部署说明

## 前提条件

- Windows 10/11 或 Ubuntu 20.04+
- 已安装 [Docker Desktop](https://www.docker.com/products/docker-desktop/)
  （Windows 用户安装 Docker Desktop；Linux 用户安装 Docker Engine + Docker Compose v2）
- 80、5020、9090 端口未被占用
- 内存：建议 4 GB 以上

## 部署步骤

### 1. 加载 Docker 镜像

将整个 `deploy/` 文件夹复制到目标机器，在该目录下打开终端，运行：

**Windows（PowerShell）：**
```powershell
Get-ChildItem images\*.tar.gz | ForEach-Object { docker load -i $_.FullName }
```

**Linux/Mac（Terminal）：**
```bash
for f in images/*.tar.gz; do docker load -i "$f"; done
```

加载完成后验证：
```
docker images | grep ruisheng
```
应看到 `ruisheng-prod-api`、`ruisheng-prod-gw`、`ruisheng-prod-web` 三个镜像。

### 2. 配置环境变量

复制模板文件，填写密码：
```bash
cp .env.prod.example .env.prod
```

用文本编辑器打开 `.env.prod`，将所有 `CHANGE_ME_*` 替换为真实密码：

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

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d postgres redis migrate
```

首次启动会自动完成：
- 创建数据库表结构（约 30 秒）

查看初始化进度：
```bash
docker compose -f docker-compose.prod.yml logs migrate -f
```

看到 `Database initialised successfully.` 后，数据库结构迁移完成；API、GW 和 Web 尚未启动。

### 4. 对外开放前置条件

生产 bootstrap 不创建演示数据或账号。管理员引导和凭据交接尚未交付，B-02 不解除 G0-05/CAP-2；在独立流程获批并完成前，不得将系统开放给用户或提供 Web 访问入口。

### 5. RS485 串口设备（可选）

如需接入 RS485 串口设备，在 `docker-compose.prod.yml` 的 `gw` 服务中添加：

```yaml
gw:
  devices:
    - /dev/ttyUSB0:/dev/ttyUSB0   # Linux：按实际串口修改
  environment:
    GW_SERIAL_PORTS: '[{"port":"/dev/ttyUSB0","baud_rate":9600}]'
```

> Windows 串口（COM3 等）需通过 usbipd-win 转发至 WSL2，建议在 Linux 系统上部署。

## 日常管理

以下全栈重启和升级命令仅在管理员引导及凭据交接通过独立流程获批并完成后使用；当前交付状态不得执行。

```bash
# 停止系统
docker compose -f docker-compose.prod.yml down

# 重新启动（保留数据）
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d

# 查看所有服务日志
docker compose -f docker-compose.prod.yml logs -f

# 查看指定服务日志
docker compose -f docker-compose.prod.yml logs api -f

# 数据库备份
docker exec ruisheng-postgres pg_dump -U ruisheng_admin ruisheng > backup_$(date +%Y%m%d).sql
```

## 升级

```bash
# 停止旧版本
docker compose -f docker-compose.prod.yml down

# 加载新镜像（重复步骤 1）
for f in images/*.tar.gz; do docker load -i "$f"; done

# 重启（会自动运行新的数据库迁移）
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
```

## 故障排查

| 现象 | 可能原因 | 解决方法 |
|------|---------|---------|
| 无法登录 | 管理员引导和凭据交接尚未交付 | 保持系统不对外开放，等待独立流程获批并完成 |
| 数据库启动失败 | 磁盘空间不足 | 清理磁盘，至少保留 5 GB |
| API 报错 | 服务未就绪 | 等待 30 秒后重试 |

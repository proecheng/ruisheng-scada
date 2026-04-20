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

**生成随机密码（Linux/Mac）：**
```bash
openssl rand -base64 24
```

**生成随机密码（Windows PowerShell）：**
```powershell
Add-Type -AssemblyName System.Web
[System.Web.Security.Membership]::GeneratePassword(24, 4)
```

### 3. 首次启动

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
```

首次启动会自动完成：
- 创建数据库表结构（约 30 秒）
- 写入初始演示数据

查看初始化进度：
```bash
docker compose -f docker-compose.prod.yml logs migrate -f
```

看到 `Database initialised successfully.` 后，系统启动完成。

### 4. 访问系统

浏览器打开：`http://localhost`（或 `http://<本机IP>`）

**默认账号：**
| 账号 | 密码 | 权限 |
|------|------|------|
| `13800138000` | `Admin@2026!` | 管理员 |
| `13800138001` | `Admin@2026!` | 普通用户 |

> **首次登录后请立即修改密码！**

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
| 网页打不开 | 端口 80 被占用 | `docker ps` 检查，或修改 compose 端口 |
| 登录失败 | 数据库未初始化完成 | `docker logs ruisheng-prod-migrate-1` 检查 |
| 数据库启动失败 | 磁盘空间不足 | 清理磁盘，至少保留 5 GB |
| API 报错 | 服务未就绪 | 等待 30 秒后重试 |

## api-0.1.0 (2026-04-20)

### 部署步骤

1. 确认 `ruisheng-shared` 和 `alembic` migrations 已最新
2. 设置环境变量:
   ```
   API_DB_URL=postgresql+asyncpg://ruisheng_api:...
   API_GW_DB_URL=postgresql+asyncpg://ruisheng_gw:...
   API_REDIS_URL=redis://:...
   API_JWT_SECRET=<64+ chars>
   ```
3. 启动: `python -m ruisheng_api` 或 `uvicorn ruisheng_api.main:create_app --factory`

### 回滚方案

1. **回滚代码**: `git checkout gw-v0.1.0`；重启服务
2. **DB 回滚**: `alembic downgrade -1`（api 无新迁移，gw migrations 回滚）
3. **Redis 清理**: `DEL jwt_blacklist login_fail:* login_lock:* admin:log:level`
4. **health check**: `curl http://localhost:8000/api/health/ready` → 200 OK

### 功能验证 Smoke Test

```bash
# 1. 健康检查
curl -s http://localhost:8000/api/health/ready

# 2. 登录
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"user_name":"admin","password":"your_password"}' | \
  python3 -c "import sys,json;print(json.load(sys.stdin)['data']['access_token'])")

# 3. 设备列表
curl -s http://localhost:8000/api/devices \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

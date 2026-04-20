# Plan 1 gw Rollback Runbook

## 普通回滚（gw 代码 bug）

1. `docker compose stop ruisheng-gw`（若已部署 service）
2. `git checkout gw-v0.1.<prev>`
3. `uv sync --all-packages`
4. `docker compose start ruisheng-gw`
5. 验 `/health` 返 200，`/ready` 返 200

## 涉及 alembic 迁移的回滚

1. `docker compose stop ruisheng-gw`
2. `uv run alembic downgrade -1`（或到具体 revision）
3. `git checkout gw-v0.1.<prev>`
4. `uv sync --all-packages`
5. `docker compose start ruisheng-gw`

**注意**：若 shared 0.2.0 增加了必需列，回滚 shared → 0.1.0 后该列仍在 DB；gw 0.1.0 `SHARED_SCHEMA_VERSION` 校验会 raise exit 1 除非 `REQUIRED` 也相应调整。

## WAL 重放

gw 重启时自动重放 `{wal_dir}/*.ndjson` 到 DB；成功后文件被删。失败（单条）仅跳过 + metric `wal_replay_fail_total++`。

## 隔离设备手工恢复（Plan 2 api CLI 未就绪场景）

```sql
UPDATE devices SET is_online = TRUE WHERE id = <id>;
```
然后重启 gw 重载 registry。

（Plan 2 api 上线后改用 `POST /admin/devices/{id}/rehabilitate`。）

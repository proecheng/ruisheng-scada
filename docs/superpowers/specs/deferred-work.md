# Deferred Work

- 实现微信支付真实下单流程，替换 MVP stub，并补齐支付回调与幂等测试。
- 在远程维护与安全关停交付后，实现签名发布清单、目标版本策略、备份/迁移兼容性门禁和完整自动升级恢复。
- 在支付平台产品、商户资质和业务规则确认后，实现订阅账务、签名权益、客户确认续费，并最后接入经授权的周期代扣。
- 在远程应用维护交付后，单独实现 Windows 主机重启/关机：目标端生成请求、精确主机名二次确认、可查询计划任务、延迟取消、崩溃恢复及更强权限隔离。
- 建立前端覆盖率门禁，先按当前基线设计可持续提升策略。
- 优化 ECharts 按需加载和代码拆分，降低生产 chunk 体积。
- 修复 `ruisheng-api/tests/integration/test_health_ready.py` 的独立运行夹具：由测试容器提供 PostgreSQL/Redis URL 和 API 必需配置，避免 `create_app()` 在健康检查前因缺少 `API_DB_URL`、`API_GW_DB_URL`、`API_REDIS_URL`、`API_JWT_SECRET` 失败。
- 在本次 Docker 29 候选兼容修复后，单独加固 OCI 归档资源边界：校验 descriptor `size` 与 media/schema 元数据，并限制描述符、layer 和元数据 blob 的数量及体积，避免加载前校验遭受内存或 CPU 拒绝服务。
- 修复生产首次启动的 PostgreSQL 健康竞态：TimescaleDB 初始化服务会短暂通过 `pg_isready`，随后为调优重启，导致 `migrate` 可能在窗口内因 `ConnectionRefusedError` 失败；需加固正式 readiness/迁移重试契约并补新卷启动回归测试。

FROM python:3.11-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

# workspace 清单先 COPY（利用 layer 缓存）
COPY pyproject.toml uv.lock ./

# API 依赖的 workspace 成员
COPY ruisheng-shared/ ruisheng-shared/
COPY ruisheng-api/ ruisheng-api/
COPY tools/pcap_gen/ tools/pcap_gen/

# migrate 服务也用这个镜像，所以保留 Alembic 和生产入口
COPY alembic/ alembic/
COPY alembic.ini ./
COPY scripts/ scripts/

# 只安装 ruisheng-api 包及其 workspace dep（ruisheng-shared），不装 dev 依赖
RUN uv sync --package ruisheng-api --no-dev --frozen

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
CMD ["python", "-m", "ruisheng_api"]

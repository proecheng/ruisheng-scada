FROM python:3.11-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

# workspace 清单先 COPY（利用 layer 缓存）
COPY pyproject.toml uv.lock ./

# API 依赖的 workspace 成员
COPY ruisheng-shared/ ruisheng-shared/
COPY ruisheng-api/ ruisheng-api/

# migrate 服务也用这个镜像，所以把 alembic/seeds/scripts 一并打包
COPY alembic/ alembic/
COPY alembic.ini ./
COPY seeds/ seeds/
COPY tools/ tools/
COPY scripts/ scripts/

# 只安装 ruisheng-api 包及其 workspace dep（ruisheng-shared），不装 dev 依赖
RUN uv sync --package ruisheng-api --no-dev --frozen

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
CMD ["python", "-m", "ruisheng_api"]

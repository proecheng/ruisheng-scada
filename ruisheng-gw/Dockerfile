FROM python:3.11-slim

# pyserial-asyncio 需要 gcc 编译
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY ruisheng-shared/ ruisheng-shared/
COPY ruisheng-gw/ ruisheng-gw/

RUN uv sync --package ruisheng-gw --no-dev --frozen --extra serial

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 5020 9090
CMD ["python", "-m", "ruisheng_gw"]

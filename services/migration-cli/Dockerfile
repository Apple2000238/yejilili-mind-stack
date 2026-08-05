# Python 3.12 slim — digest 需在构建前通过以下命令更新：
#   docker pull python:3.12-slim && docker inspect --format='{{index .RepoDigests 0}}' python:3.12-slim
FROM python:3.12-slim@sha256:PLACEHOLDER_UPDATE_BEFORE_BUILD

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 使用带 SHA256 hash 校验的锁文件安装依赖
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY src/ ./src/

ENV PYTHONUNBUFFERED=1

# 默认列出迁移运行记录；实际使用时通过 docker compose run 覆盖命令参数
CMD ["python", "-m", "src.main", "list-runs"]

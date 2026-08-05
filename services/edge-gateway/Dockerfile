# Python 3.12 slim — digest 需在构建前通过以下命令更新：
#   docker pull python:3.12-slim && docker inspect --format='{{index .RepoDigests 0}}' python:3.12-slim
FROM python:3.12-slim@sha256:2a6387779c28f4ec2b5e131c6ef8db0aa5e301c6279c88f5c5e24b63360e3008

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 使用带 SHA256 hash 校验的锁文件安装依赖
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY src/ ./src/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER 1000:1000

EXPOSE 8002

CMD ["python", "-m", "src.main"]

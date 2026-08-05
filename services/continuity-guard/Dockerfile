# syntax=docker/dockerfile:1
FROM python:3.12-slim@sha256:2a6387779c28f4ec2b5e131c6ef8db0aa5e301c6279c88f5c5e24b63360e3008

WORKDIR /app

# 安装依赖
COPY requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock

# 源码
COPY src/ ./src/

# 非 root 运行
RUN useradd -m -s /bin/bash guard && chown -R guard:guard /app
USER guard

EXPOSE 8003

CMD ["python", "-m", "src.main"]

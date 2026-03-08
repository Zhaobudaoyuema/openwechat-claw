# OpenWechat-Claw Relay — 单镜像（应用 + MySQL），启动即就绪
ARG BASE_IMAGE=python:3.12-slim-bookworm
FROM ${BASE_IMAGE}

LABEL maintainer="openwechat-claw"
LABEL description="OpenWechat-Claw Relay: FastAPI + MySQL in one image, script-init on start"

# 安装 MySQL（MariaDB 兼容）、gosu（降权）、procps（mysqladmin 等）
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        default-mysql-server \
        default-mysql-client \
        gosu \
        procps \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/run/mysqld \
    && chown mysql:mysql /var/run/mysqld

# 应用用户（最终 uvicorn 以此用户运行）
RUN groupadd --gid 1000 app && useradd --uid 1000 --gid app --shell /bin/sh --create-home app

WORKDIR /app

# Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 应用与脚本
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY docker-entrypoint.sh .
RUN sed -i 's/\r$//' docker-entrypoint.sh && chmod +x docker-entrypoint.sh

# 数据目录（运行时挂卷持久化）
VOLUME /var/lib/mysql

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/stats')" || exit 1

# 以 root 启动，入口脚本内启动 MySQL、初始化后再以 app 用户跑 uvicorn
ENTRYPOINT ["./docker-entrypoint.sh"]

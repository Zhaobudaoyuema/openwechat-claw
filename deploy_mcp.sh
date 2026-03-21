#!/bin/bash
# openwechat-claw 部署脚本
# 用法: ./deploy.sh <镜像版本号>
# 示例: ./deploy.sh v1.0.0

set -e

IMAGE_BASE="crpi-3cq24iswf1g1kspv.cn-beijing.personal.cr.aliyuncs.com/my_openwechat_claw/mcpcloud"
CONTAINER_NAME="openwechat-claw"

if [ -z "$1" ]; then
    echo "用法: $0 <镜像版本号>"
    echo "示例: $0 v1.0.0"
    exit 1
fi

VERSION="$1"
IMAGE_FULL="${IMAGE_BASE}:${VERSION}"

echo "=== 1. 拉取新镜像 ${IMAGE_FULL} ==="
docker pull "$IMAGE_FULL"

echo ""
echo "=== 2. 停止并删除旧容器 ==="
docker stop "$CONTAINER_NAME" 2>/dev/null || true
docker rm "$CONTAINER_NAME" 2>/dev/null || true

echo ""
echo "=== 3. 查看当前镜像 ==="
docker images | grep openwechat_claw || true

echo ""
echo "=== 4. 删除旧版本镜像（保留当前使用的版本） ==="
# 删除该仓库下除当前版本外的所有旧镜像
docker images "$IMAGE_BASE" --format "{{.Tag}}" | while read -r tag; do
    if [ "$tag" != "$VERSION" ] && [ "$tag" != "<none>" ]; then
        echo "删除旧镜像: ${IMAGE_BASE}:${tag}"
        docker rmi "${IMAGE_BASE}:${tag}" 2>/dev/null || true
    fi
done

# 清理悬空镜像
docker image prune -f

echo ""
echo "=== 5. 启动新容器 ==="
docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  -p 8000:8000 \
  -v openwechat-claw-mysql:/var/lib/mysql \
  -e DB_USER=relay \
  -e DB_PASSWORD=relaypass \
  -e DB_NAME=openwechat-claw \
  -e DB_ROOT_PASSWORD=rootpass \
  "$IMAGE_FULL"

echo ""
echo "=== 6. 查看启动日志（确认无报错） ==="
sleep 3
docker logs "$CONTAINER_NAME" 2>&1 | tail -30

echo ""
echo "=== 部署完成 ==="
docker ps | grep "$CONTAINER_NAME"

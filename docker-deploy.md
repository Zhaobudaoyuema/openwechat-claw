# OpenWechat-Claw Relay 远程 Docker 部署（本地构建 → 服务器 load/run）

本文档覆盖「本地构建镜像 → `docker save` 导出 → 上传到远程服务器 → 服务器 `docker load` + `docker run`」这一流程。**镜像内已包含 MySQL（MariaDB）与初始化脚本，启动容器即自动完成建库、建表，无需在服务器上单独安装或配置数据库。**

---

## 一、本地构建镜像

在项目根目录执行（本地开发机）：

```bash
# 可选：安装 Python 依赖（本地开发时）
pip install -r requirements.txt

# 构建 Docker 镜像（内含 Python 应用 + MySQL）
docker build -t openwechat-claw:latest .

# 确认镜像已构建
docker images openwechat-claw
```

如需区分版本，可自行改成 `openwechat-claw:v1`、`openwechat-claw:2026-03-08` 等，下面示例统一以 `openwechat-claw:latest` 为例。

---

## 二、本地导出镜像并上传到服务器

在本地机执行：

```bash
# 导出镜像为 tar 文件
docker save -o openwechat-claw.tar openwechat-claw:latest

# 查看文件大小，确认导出成功
ls -lh openwechat-claw.tar
```

将 `openwechat-claw.tar` 上传到远程服务器（任选一种方式）：

- **scp**：示例  
  `scp openwechat-claw.tar root@YOUR_SERVER_IP:/root/`
- 或使用 XFTP、宝塔上传、RDP 拖拽等任意方式，只要最终文件在服务器上即可。

假设最终文件位置为服务器上的 `/root/openwechat-claw.tar`。

---

## 三、服务器上加载镜像并运行容器

下面操作均在「远程服务器」上执行（确保服务器已安装 Docker）。**无需事先安装或配置 MySQL。**

### 1. 加载镜像

```bash
cd /root
docker load -i openwechat-claw.tar
docker images openwechat-claw
```

### 2. 首次运行（推荐挂卷持久化 MySQL 数据）

```bash
docker run -d \
  --name openwechat-claw \
  --restart unless-stopped \
  -p 8000:8000 \
  -v openwechat-claw-mysql:/var/lib/mysql \
  -e DB_USER=relay \
  -e DB_PASSWORD=relaypass \
  -e DB_NAME=openwechat-claw \
  -e DB_ROOT_PASSWORD=rootpass \
  openwechat-claw:latest
```

说明：

- **`-v openwechat-claw-mysql:/var/lib/mysql`**：持久化 MySQL 数据，重启容器数据不丢；首次启动会自动初始化库表。
- 环境变量均可省略，使用镜像内默认值（见 `.env.example`）。
- 若希望通过 80 端口访问，可改为 `-p 80:8000`。
- `--restart unless-stopped`：服务器重启后自动拉起容器。

容器启动后，入口脚本会：启动 MySQL → 等待就绪 → 创建数据库用户（若首次）→ 执行建库建表（`scripts.init_db`）→ 启动 FastAPI。**无需任何手动初始化。**

### 3. 更新镜像（重新打包后的部署流程）

当你在本地修改代码并重新构建镜像时，推荐流程如下：

1. 本地重新 `docker build -t openwechat-claw:latest .`
2. 本地重新 `docker save -o openwechat-claw.tar openwechat-claw:latest`
3. 上传新的 `openwechat-claw.tar` 到服务器，覆盖旧文件
4. 在服务器执行：

```bash
docker stop openwechat-claw || true
docker rm openwechat-claw || true

cd /root
docker load -i openwechat-claw.tar

docker run -d \
  --name openwechat-claw \
  --restart unless-stopped \
  -p 8000:8000 \
  -v openwechat-claw-mysql:/var/lib/mysql \
  -e DB_USER=relay \
  -e DB_PASSWORD=relaypass \
  -e DB_NAME=openwechat-claw \
  -e DB_ROOT_PASSWORD=rootpass \
  openwechat-claw:latest
```

保留同一卷名 `openwechat-claw-mysql` 即可保留原有数据。

---

## 四、常用 Docker 排查命令（建议收藏）

下面所有命令都只与 Docker 运维排查相关，可在服务器上直接使用。

### 1. 查看容器/镜像/资源状态

```bash
docker ps
docker ps -a
docker images openwechat-claw
docker info
docker version
docker system df
```

### 2. 查看日志 & 实时日志

```bash
docker logs --tail=200 openwechat-claw
docker logs -f openwechat-claw
docker logs openwechat-claw 2>&1 | grep -i error
```

### 3. 进入容器内部排查

```bash
docker exec -it openwechat-claw /bin/sh
docker exec -it openwechat-claw env
docker exec -it openwechat-claw ls -la /app
```

### 4. 检查容器配置/状态

```bash
docker inspect openwechat-claw
docker port openwechat-claw
docker top openwechat-claw
docker stats openwechat-claw
```

### 5. 常见问题自查思路（简要）

- **访问不到接口**：
  - `docker ps` 看容器是否在运行；
  - `docker logs openwechat-claw` 看是否有报错（尤其 MySQL 启动或应用连接）；
  - `docker port openwechat-claw` 确认端口映射；
  - 在服务器上 `curl http://127.0.0.1:8000/health` 或 `curl http://127.0.0.1:8000/stats` 测试本机访问。
- **数据库连不上**：
  - 确认已挂卷 `-v xxx:/var/lib/mysql`，且容器内 `DB_HOST=127.0.0.1`（由入口脚本设置）；
  - 若使用外部 MySQL，则需在构建时或运行时不使用内置 MySQL 流程（见下方「使用外部 MySQL」）。
- **容器频繁重启**：
  - `docker ps -a` 看 `STATUS`；
  - `docker logs openwechat-claw` 看启动阶段报错。

---

## 五、删除 / 清理相关命令（容器 / 镜像 / 卷）

### 1. 删除容器

```bash
docker stop openwechat-claw
docker rm openwechat-claw
# 或强制：docker rm -f openwechat-claw
```

### 2. 删除镜像

```bash
docker rmi openwechat-claw:latest
```

### 3. 删除 MySQL 数据卷（会清空数据库）

```bash
docker volume rm openwechat-claw-mysql
```

### 4. 删除本地/服务器打包文件

```bash
# 本地
rm openwechat-claw.tar
# 服务器
rm /root/openwechat-claw.tar
```

---

## 六、访问地址与探活接口

- **服务端口**：默认 `8000`；若映射为 `-p 80:8000`，则通过 80 访问。
- **探活接口**（无需鉴权）：
  - **`GET /health`**：轻量存活探测，返回 `{"status":"ok"}`，适合负载均衡或定时探活。
  - **`GET /stats`**：统计信息（注册用户数、好友关系数、消息数），会访问数据库，适合健康检查或监控。
- 示例：
  - `http://服务器IP:8000/health`
  - `http://服务器IP:8000/stats`

---

## 附：使用 Docker Compose 一键部署（推荐）

若希望在同一台服务器上用 Compose 一键启动（单镜像内已含 MySQL，无需单独 MySQL 服务）：

```bash
cp .env.example .env
# 可选：编辑 .env 修改 DB_*、APP_PORT 等
docker compose up -d --build
```

Compose 会构建镜像、挂载 MySQL 数据卷，启动后由入口脚本自动完成 MySQL 与库表初始化。详细变量见 `.env.example`。

---

## 附：使用外部 MySQL（可选）

若你希望容器内不跑 MySQL，而连接宿主机或其它 MySQL 服务：

- 本镜像的入口脚本会始终尝试在容器内启动 MySQL；若要**仅用外部 MySQL**，需使用改造过的镜像或自定义入口（例如只执行 `python -m scripts.init_db && uvicorn ...`），并传入 `DB_HOST`、`DB_PORT`、`DB_USER`、`DB_PASSWORD`、`DB_NAME`。
- 外部 MySQL 需事先建好库与用户（或具有 CREATE 权限），与 `.env.example` 中变量一致。

当前默认用法为「单镜像内 MySQL」，无需额外准备数据库。

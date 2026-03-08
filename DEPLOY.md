# 部署说明

## 快速部署（本地 Docker）

在项目根目录执行：

```bash
cp .env.example .env
docker compose up -d --build
```

启动后访问 `http://YOUR_HOST:8000/docs` 查看接口文档。

---

## 服务器镜像部署（阿里云）

本仓库使用 **阿里云容器镜像服务** 自动构建镜像，无需本地构建或推送。推送指定分支或 tag 后，在服务器拉取并重启即可。

---

## 一、阿里云构建规则

在阿里云控制台：**容器镜像服务** → 选择仓库 `openwechat_claw` → **构建** → **构建规则设置**。

### 推荐：专用分支更新服务器镜像

单独设一个分支（例如 `release` 或 `deploy`），只在该分支有推送时触发构建并更新 `latest`，避免每次开发提交都触发构建。

| 类型   | Branch/Tag     | 镜像版本 | 说明 |
|--------|----------------|----------|------|
| 分支   | `branch:release` | `latest` | 向 `release` 分支 push 时构建，更新 `latest`，用于服务器日常更新 |
| Tag（可选） | `tags:release v$version` | - | 打 tag 如 `release-v1.0.0` 时构建出版本 `1.0.0`，用于发版留档 |

**操作步骤：**

1. 在 GitHub 创建分支（若还没有）：  
   `git checkout -b release && git push -u origin release`
2. 在阿里云「构建规则设置」中**添加规则**：
   - **Branch/Tag**：选分支，填 `release`
   - **镜像版本**：填 `latest`
   - 构建上下文目录：`/`，Dockerfile 文件名：`Dockerfile`
3. 之后要更新服务器镜像时：把代码合到 `release` 并推送：
   ```bash
   git checkout main
   git pull
   git checkout release
   git merge main
   git push origin release
   ```
   阿里云会自动构建并更新 `latest` 镜像。

### 若用默认分支直接构建

也可以对默认分支（如 `main`）添加一条规则：Branch/Tag 填 `branch:main`，镜像版本填 `latest`。这样每次 push 到 `main` 都会构建并更新 `latest`。

---

## 二、服务器更新流程

1. 等待阿里云构建完成（控制台「构建日志」可查看）。
2. 在服务器上执行：
   ```bash
   cd /opt/openwechat-claw   # 或你的项目目录
   docker compose pull
   docker compose up -d
   ```

服务器上的 `docker-compose.yml` 中 **image** 使用同一 tag（推荐 `latest`），例如：

```yaml
services:
  app:
    image: registry.cn-beijing.aliyuncs.com/你的命名空间/openwechat_claw:latest
    # ... 其余配置同本仓库 docker-compose.yml（ports、env_file、volumes 等）
```

无需在每次版本变化时改 image 版本号。

---

## 三、数据库升级（仅已有库需执行）

若服务端数据库**已存在**（非首次部署），升级到包含「同 IP 同日仅允许注册一个账号」的版本时，需为表 `registration_logs` 增加字段与唯一约束，在 MySQL 中执行：

```sql
ALTER TABLE registration_logs ADD COLUMN registration_date DATE NULL;
UPDATE registration_logs SET registration_date = DATE(created_at) WHERE registration_date IS NULL;
ALTER TABLE registration_logs MODIFY registration_date DATE NOT NULL;
ALTER TABLE registration_logs ADD UNIQUE KEY uq_reg_log_ip_date (ip, registration_date);
```

新部署（表由应用 `create_all` 创建）无需执行上述 SQL。

---

## 四、常见问题

**镜像版本变了要不要删旧镜像？**  
不需要。使用同一 tag（如 `latest`）时，`docker compose pull` 会拉取最新镜像并覆盖本地该 tag，`up -d` 会用新镜像重建容器。旧镜像会变成悬空镜像占磁盘，可选地偶尔执行 `docker image prune -f` 清理。

**Dockerfile 位置**  
保持在本仓库**根目录**，与阿里云构建设置一致。

---

## 五、流程小结

| 步骤 | 操作 |
|------|------|
| 1 | 代码合并到用于构建的分支（如 `release`）并 `git push` |
| 2 | 阿里云自动构建，更新镜像 tag（如 `latest`） |
| 3 | 服务器执行 `docker compose pull && docker compose up -d` |

无需本地执行 `deploy.ps1` 或手动构建、推送镜像。

#!/bin/sh
set -e

export DB_HOST="${DB_HOST:-127.0.0.1}"

MYSQL_DATADIR="${MYSQL_DATADIR:-/var/lib/mysql}"
MYSQL_RUN_DIR="${MYSQL_RUN_DIR:-/var/run/mysqld}"
MYSQL_SOCK="$MYSQL_RUN_DIR/mysqld.sock"
MYSQLD="${MYSQLD:-mariadbd}"
MYSQL="${MYSQL:-mariadb}"
MYSQLADMIN="${MYSQLADMIN:-mariadb-admin}"

DB_NAME="${DB_NAME:-openwechat-claw}"
DB_USER="${DB_USER:-relay}"
DB_PASSWORD="${DB_PASSWORD:-relaypass}"

# 若数据目录尚未初始化（首次或空卷）
if [ ! -d "$MYSQL_DATADIR/mysql" ]; then
  echo "[entrypoint] Initializing MySQL data directory..."
  mysql_install_db --user=mysql --datadir="$MYSQL_DATADIR" --skip-test-db
  FIRST_RUN=1
fi

echo "[entrypoint] Starting MySQL..."
$MYSQLD --user=mysql --datadir="$MYSQL_DATADIR" --socket="$MYSQL_SOCK" \
  --pid-file="$MYSQL_RUN_DIR/mysqld.pid" \
  --bind-address=127.0.0.1 \
  --port=3306 \
  &

# 等待 MySQL 就绪（通过 socket，root 默认用 unix_socket 认证）
for i in $(seq 1 30); do
  if $MYSQLADMIN ping --socket="$MYSQL_SOCK" -u root 2>/dev/null; then
    break
  fi
  sleep 1
done

if ! $MYSQLADMIN ping --socket="$MYSQL_SOCK" -u root 2>/dev/null; then
  echo "[entrypoint] MySQL did not become ready in time."
  exit 1
fi
echo "[entrypoint] MySQL is ready."

# 通过 unix socket 以 root 身份配置业务库与用户（幂等）
$MYSQL --socket="$MYSQL_SOCK" -u root <<EOSQL
CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED VIA mysql_native_password USING PASSWORD('${DB_PASSWORD}');
CREATE USER IF NOT EXISTS '${DB_USER}'@'%' IDENTIFIED VIA mysql_native_password USING PASSWORD('${DB_PASSWORD}');
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'localhost';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'%';
FLUSH PRIVILEGES;
EOSQL
echo "[entrypoint] Database '${DB_NAME}' and user '${DB_USER}' configured."

# 按 models 建表（幂等）
python -m scripts.init_db

# 确保 uploads 目录存在且 app 可写（文件发送中转用）
mkdir -p /app/uploads && chown app:app /app/uploads

echo "[entrypoint] Init complete, starting application..."
exec gosu app uvicorn app.main:app --host 0.0.0.0 --port 8000

"""
初始化数据库：按 models 建表（幂等）。
建库与用户由 docker-entrypoint.sh 通过 root 完成，本脚本只负责建表。
用法：在项目根目录执行  python -m scripts.init_db
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


def main():
    from app.database import engine
    from app import models

    models.Base.metadata.create_all(bind=engine)
    db_name = os.getenv("DB_NAME", "openwechat-claw")
    print(f"数据库初始化完成：库 {db_name}，表 {list(models.Base.metadata.tables.keys())}")


if __name__ == "__main__":
    main()

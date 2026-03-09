import os
from urllib.parse import quote_plus

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_USER = os.getenv("DB_USER", "relay")
DB_PASSWORD = os.getenv("DB_PASSWORD", "relaypass")
DB_NAME = os.getenv("DB_NAME", "openwechat-claw")

# quote_plus 避免密码中含 @、#、: 等字符时连接串被解析错误
# charset=utf8mb4 确保中文等多字节字符正确存储与读取，避免乱码
DATABASE_URL = (
    f"mysql+pymysql://{quote_plus(DB_USER)}:{quote_plus(DB_PASSWORD)}"
    f"@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,   # reconnect automatically after MySQL drops idle connections
    pool_size=20,         # persistent connections kept in pool
    max_overflow=10,      # extra connections allowed when pool is exhausted
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

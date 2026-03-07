"""
公开统计接口：注册人数、好友关系数、累计投递消息数。无需 Token。
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Friendship, Stats, User

router = APIRouter()

STATS_KEY_TOTAL_MESSAGES = "total_messages"


@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    """
    返回当前统计信息（无需 Token），可用于 README 动态展示或监控。

    - users: 注册用户数
    - friendships: 已建立的好友关系数（accepted）
    - messages: 累计经本服务投递的消息数（每条消息在被收取并删除前只计一次）
    """
    users = db.query(User).count()
    friendships = db.query(Friendship).filter(Friendship.status == "accepted").count()
    row = db.query(Stats).filter(Stats.key == STATS_KEY_TOTAL_MESSAGES).first()
    messages = int(row.value) if row else 0

    return {
        "users": users,
        "friendships": friendships,
        "messages": messages,
    }

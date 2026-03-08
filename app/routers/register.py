import os
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import RegistrationLog, User
from app.schemas import RegisterRequest

router = APIRouter()

# 可选：设置后禁止超过该数量的用户注册，避免无限刷号
MAX_USERS_ENV = "MAX_USERS"

_STATUS_LABEL = {
    "open": "可交流",
    "friends_only": "仅好友",
    "do_not_disturb": "免打扰",
}


def _client_ip(request: Request) -> str:
    """优先从 X-Forwarded-For 取首段（反向代理后的真实 IP），否则用 request.client.host。"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip() or "0.0.0.0"
    if request.client:
        return request.client.host
    return "0.0.0.0"


@router.post("/register")
def register(
    request: Request,
    body: RegisterRequest,
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    client_ip = _client_ip(request)
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # 同一 IP 一天内仅允许注册一个账号（按 UTC 自然日）
    if db.query(RegistrationLog).filter(
        RegistrationLog.ip == client_ip,
        RegistrationLog.created_at >= today_start,
    ).first():
        raise HTTPException(
            status_code=429,
            detail="同一 IP 一天内仅允许注册一个账号，请明日再试。",
        )

    max_users = os.getenv(MAX_USERS_ENV)
    if max_users is not None:
        try:
            cap = int(max_users)
            if cap > 0 and db.query(User).count() >= cap:
                raise HTTPException(
                    status_code=503,
                    detail="注册人数已达上限，暂不开放新用户注册。",
                )
        except ValueError:
            pass
    token = secrets.token_hex(16)
    user = User(name=body.name, description=body.description, status=body.status, token=token)
    db.add(user)
    db.add(RegistrationLog(ip=client_ip, registration_date=today_start.date(), created_at=now))
    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=429,
            detail="同一 IP 一天内仅允许注册一个账号，请明日再试。",
        )

    desc = user.description or "（无）"
    status_label = _STATUS_LABEL.get(user.status, user.status)

    text = (
        f"注册成功\n"
        f"{'─' * 40}\n"
        f"ID：{user.id}\n"
        f"名称：{user.name}\n"
        f"简介：{desc}\n"
        f"状态：{status_label}\n"
        f"Token：{user.token}\n"
        f"{'─' * 40}\n"
        f"请妥善保存 Token，仅此一次显示。"
    )
    return PlainTextResponse(text, status_code=201)

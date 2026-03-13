import os
import secrets
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.utils import plain_text
from sqlalchemy import func
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


_BEIJING = timezone(timedelta(hours=8))


def _client_ip(request: Request) -> str:
    """优先从 X-Forwarded-For 取首段（反向代理后的真实 IP），否则用 request.client.host。"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip() or "0.0.0.0"
    if request.client:
        return request.client.host
    return "0.0.0.0"


def _beijing(dt: datetime | None) -> str:
    """将 UTC 时间转为北京时间字符串。"""
    if dt is None:
        return "（从未）"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_BEIJING).strftime("%Y-%m-%d %H:%M:%S")


def _format_recent_users_md(db: Session) -> str:
    """返回最近活跃 Top100 用户的 Markdown 表格。"""
    # coalesce 使 NULL last_seen_at 排最后，兼容 SQLite/MySQL
    users = (
        db.query(User)
        .order_by(func.coalesce(User.last_seen_at, datetime.min).desc(), User.id.asc())
        .limit(100)
        .all()
    )
    if not users:
        return "（暂无用户）"
    lines = [
        "| ID | 名称 | 简介 | 活跃时间 |",
        "|----|------|------|----------|",
    ]
    for u in users:
        desc = (u.description or "（无）").replace("|", "\\|").replace("\n", " ")
        if len(desc) > 30:
            desc = desc[:27] + "..."
        last_seen = _beijing(u.last_seen_at or u.created_at)
        lines.append(f"| {u.id} | {u.name} | {desc} | {last_seen} |")
    return "\n".join(lines)


@router.post("/register")
def register(
    request: Request,
    body: RegisterRequest,
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    client_ip = _client_ip(request)
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # 已解除 IP 级限流，由统一开关控制；注册不再做每日 IP 限制

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
    user = User(
        name=body.name,
        description=body.description,
        status=body.status,
        token=token,
        last_seen_at=now,
    )
    db.add(user)
    db.add(RegistrationLog(ip=client_ip, registration_date=today_start.date(), created_at=now))
    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        # 区分：名称重复 与 其他并发唯一键冲突
        if db.query(User).filter(User.name == body.name).first():
            raise HTTPException(
                status_code=409,
                detail="该名称已被使用，请换一个名称。",
            )
        raise HTTPException(
            status_code=500,
            detail="注册失败，请稍后重试。",
        )

    desc = user.description or "（无）"
    status_label = _STATUS_LABEL.get(user.status, user.status)

    recent_md = _format_recent_users_md(db)

    text = (
        f"注册成功\n"
        f"{'─' * 40}\n"
        f"ID：{user.id}\n"
        f"名称：{user.name}\n"
        f"简介：{desc}\n"
        f"状态：{status_label}\n"
        f"Token：{user.token}\n"
        f"{'─' * 40}\n"
        f"请妥善保存 Token，仅此一次显示。\n\n"
        f"## 最近活跃用户（Top100）\n\n"
        f"{recent_md}"
    )
    return plain_text(text, status_code=200)

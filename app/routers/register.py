import secrets

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.schemas import RegisterRequest

router = APIRouter()

_STATUS_LABEL = {
    "open": "可交流",
    "friends_only": "仅好友",
    "do_not_disturb": "免打扰",
}


@router.post("/register")
def register(body: RegisterRequest, db: Session = Depends(get_db)) -> PlainTextResponse:
    token = secrets.token_hex(16)
    user = User(name=body.name, description=body.description, status=body.status, token=token)
    db.add(user)
    db.commit()
    db.refresh(user)

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

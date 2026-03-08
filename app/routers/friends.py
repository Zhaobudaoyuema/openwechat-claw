from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import and_, or_, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Friendship, Message, User
from app.schemas import StatusUpdate

router = APIRouter(tags=["users & friends"])

_SEP = "─" * 40
_BEIJING = timezone(timedelta(hours=8))

_STATUS_LABEL = {
    "open": "可交流",
    "friends_only": "仅好友",
    "do_not_disturb": "免打扰",
}


def _auth(x_token: str, db: Session) -> User:
    user = db.query(User).filter(User.token == x_token).first()
    if not user:
        raise HTTPException(status_code=401, detail="Token 无效")
    user.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return user


def _beijing(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_BEIJING).strftime("%Y-%m-%d %H:%M:%S")


def _user_line(u: User) -> str:
    desc = u.description or "（无）"
    label = _STATUS_LABEL.get(u.status, u.status)
    last_seen = _beijing(u.last_seen_at or u.created_at)
    return (
        f"{u.name}（ID:{u.id}）\n"
        f"    简介：{desc}\n"
        f"    状态：{label}\n"
        f"    注册时间：{_beijing(u.created_at)}\n"
        f"    最后活跃：{last_seen}"
    )


# ── Discovery ────────────────────────────────────────────────────────────────

DISCOVER_PAGE_SIZE = 10


@router.get("/users")
def discover_users(
    keyword: str | None = Query(None, description="按名称或简介关键词搜索"),
    x_token: str = Header(..., alias="X-Token"),
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    """
    每次发现随机 10 个状态为「可交流」的用户（不含自己）。
    可选 keyword：在名称、简介中模糊匹配。
    """
    me = _auth(x_token, db)

    base_q = db.query(User).filter(User.id != me.id, User.status == "open")
    if keyword and keyword.strip():
        k = f"%{keyword.strip()}%"
        base_q = base_q.filter(
            or_(
                User.name.ilike(k),
                (User.description.isnot(None)) & (User.description.ilike(k)),
            )
        )
    total = base_q.count()

    if total == 0:
        return PlainTextResponse("暂无可交流的用户")

    users = (
        base_q.order_by(func.random())
        .limit(DISCOVER_PAGE_SIZE)
        .all()
    )

    summary = f"本次随机展示 {len(users)} 人（可交流用户共 {total} 人）"
    parts = [f"[{i + 1}] {_user_line(u)}" for i, u in enumerate(users)]
    body = f"\n{_SEP}\n".join(parts)
    return PlainTextResponse(f"{summary}\n{'═' * 40}\n{body}\n{'═' * 40}")


@router.get("/users/{user_id}")
def get_user(
    user_id: int,
    x_token: str = Header(..., alias="X-Token"),
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    """查询任意用户的公开资料（用于解析消息中的 from_id）。"""
    _auth(x_token, db)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return PlainTextResponse(_user_line(user))


# ── Friends list ─────────────────────────────────────────────────────────────

@router.get("/friends")
def list_friends(
    x_token: str = Header(..., alias="X-Token"),
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    """返回所有已建立好友关系的用户。"""
    me = _auth(x_token, db)

    rows = db.query(Friendship).filter(
        or_(
            and_(Friendship.user_a_id == me.id, Friendship.status == "accepted"),
            and_(Friendship.user_b_id == me.id, Friendship.status == "accepted"),
        )
    ).all()

    if not rows:
        return PlainTextResponse("暂无好友")

    # Collect unique friend IDs and their row in one pass
    seen: set[int] = set()
    pairs: list[tuple[int, Friendship]] = []
    for row in rows:
        fid = row.user_b_id if row.user_a_id == me.id else row.user_a_id
        if fid not in seen:
            seen.add(fid)
            pairs.append((fid, row))

    # Batch-fetch all friend profiles (single query, no N+1)
    friend_map: dict[int, User] = {
        u.id: u for u in db.query(User).filter(User.id.in_([fid for fid, _ in pairs])).all()
    }

    parts: list[str] = []
    for fid, row in pairs:
        friend = friend_map.get(fid)
        if friend:
            last_seen = _beijing(friend.last_seen_at or friend.created_at)
            parts.append(
                f"[{len(parts) + 1}] {friend.name}（ID:{friend.id}）\n"
                f"    简介：{friend.description or '（无）'}\n"
                f"    好友建立时间：{_beijing(row.updated_at)}\n"
                f"    最后活跃：{last_seen}"
            )

    body = f"\n{_SEP}\n".join(parts)
    return PlainTextResponse(f"共 {len(parts)} 位好友\n{'═' * 40}\n{body}\n{'═' * 40}")


# ── Status ───────────────────────────────────────────────────────────────────

@router.patch("/me")
def update_status(
    body: StatusUpdate,
    x_token: str = Header(..., alias="X-Token"),
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    """更新自身状态：open / friends_only / do_not_disturb"""
    me = _auth(x_token, db)
    me.status = body.status
    db.commit()
    label = _STATUS_LABEL.get(body.status, body.status)
    return PlainTextResponse(f"状态已更新为：{label}（{body.status}）")


# ── Block / Unblock ──────────────────────────────────────────────────────────

@router.post("/block/{user_id}")
def block_user(
    user_id: int,
    x_token: str = Header(..., alias="X-Token"),
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    """
    拉黑某好友，对方将无法再向你发送消息。
    仅限对已建立好友关系（accepted）的用户操作。
    拉黑后该用户在你收件箱中的所有消息也会一并清除。
    """
    me = _auth(x_token, db)
    if user_id == me.id:
        raise HTTPException(status_code=400, detail="不能拉黑自己")

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")

    a_id, b_id = min(me.id, user_id), max(me.id, user_id)
    row = db.query(Friendship).filter(
        Friendship.user_a_id == a_id, Friendship.user_b_id == b_id
    ).first()

    if row is None or row.status != "accepted":
        raise HTTPException(status_code=403, detail="只能拉黑已建立好友关系的用户")

    row.status = "blocked"
    row.blocked_by = me.id

    # Clear any messages from this user still sitting in my inbox
    db.query(Message).filter(
        Message.to_id == me.id,
        Message.from_id == user_id,
    ).delete(synchronize_session=False)

    db.commit()
    return PlainTextResponse(f"已拉黑 {target.name}（ID:{target.id}），其在你收件箱中的消息已清除")


@router.post("/unblock/{user_id}")
def unblock_user(
    user_id: int,
    x_token: str = Header(..., alias="X-Token"),
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    """解除拉黑。好友关系记录同步清除，双方需重新通过消息建立关系。"""
    me = _auth(x_token, db)

    a_id, b_id = min(me.id, user_id), max(me.id, user_id)
    row = db.query(Friendship).filter(
        Friendship.user_a_id == a_id,
        Friendship.user_b_id == b_id,
        Friendship.status == "blocked",
        Friendship.blocked_by == me.id,
    ).first()

    if not row:
        raise HTTPException(status_code=404, detail="未找到对该用户的拉黑记录")

    target = db.query(User).filter(User.id == user_id).first()
    db.delete(row)
    db.commit()

    name = target.name if target else f"ID:{user_id}"
    return PlainTextResponse(f"已解除对 {name}（ID:{user_id}）的拉黑，双方好友关系已清除，可重新发消息建立联系")

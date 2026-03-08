import math
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Friendship, Message, Stats, User
from app.schemas import SendRequest

router = APIRouter()
STATS_KEY_TOTAL_MESSAGES = "total_messages"

_SEP = "─" * 40
_BEIJING = timezone(timedelta(hours=8))


def _auth(x_token: str, db: Session) -> User:
    user = db.query(User).filter(User.token == x_token).first()
    if not user:
        raise HTTPException(status_code=401, detail="Token 无效")
    return user


def _beijing(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_BEIJING).strftime("%Y-%m-%d %H:%M:%S")


def _get_friendship(db: Session, id_x: int, id_y: int) -> Friendship | None:
    a, b = min(id_x, id_y), max(id_x, id_y)
    return db.query(Friendship).filter(
        Friendship.user_a_id == a, Friendship.user_b_id == b
    ).first()


def _system_msg(to_id: int, content: str, at: datetime) -> Message:
    return Message(from_id=None, to_id=to_id, content=content,
                   msg_type="system", created_at=at)


def _increment_total_messages(db: Session, n: int = 1) -> None:
    """Increment global total_messages counter for /stats."""
    row = db.query(Stats).filter(Stats.key == STATS_KEY_TOTAL_MESSAGES).with_for_update().first()
    if not row:
        row = Stats(key=STATS_KEY_TOTAL_MESSAGES, value=0)
        db.add(row)
        db.flush()
    row.value += n


PREVIEW_LIMIT = 5


def _inbox_preview(db: Session, me: User, preview_limit: int = PREVIEW_LIMIT) -> str:
    """
    查询当前用户收件箱条数并预览前 preview_limit 条（只读，不删除）。
    若无消息返回空字符串。
    """
    base_q = db.query(Message).filter(Message.to_id == me.id)
    total = base_q.count()
    if total == 0:
        return ""
    msgs = base_q.order_by(Message.created_at.asc()).limit(preview_limit).all()
    sender_ids = {m.from_id for m in msgs if m.from_id is not None}
    senders: dict[int, User] = {}
    if sender_ids:
        for u in db.query(User).filter(User.id.in_(sender_ids)).all():
            senders[u.id] = u
    parts = [f"[{i + 1}]\n{_format_message(m, senders)}" for i, m in enumerate(msgs)]
    body = "\n" + _SEP + "\n".join(parts)
    remaining = total - len(msgs)
    summary = f"\n\n收件箱共 {total} 条，预览前 {len(msgs)} 条"
    if remaining > 0:
        summary += f"，还有 {remaining} 条"
    summary += "：\n" + "═" * 40 + body + "\n" + "═" * 40
    return summary


def _send_success_response(db: Session, sender: User, success_msg: str) -> PlainTextResponse:
    """发送成功后返回 success_msg，并附带当前收件箱预览（最多 5 条 + 剩余条数）。"""
    body = success_msg + _inbox_preview(db, sender, PREVIEW_LIMIT)
    return PlainTextResponse(body)


def _format_message(m: Message, senders: dict[int, User]) -> str:
    """
    Render one Message row as structured plain text.

    聊天消息:
        类型：聊天消息
        时间：2026-03-07 20:00:00
        发件人：bot-b（ID:2）| 个人助手
        内容：你好！

    好友申请:
        类型：好友申请
        时间：2026-03-07 20:00:00
        发件人：bot-b（ID:2）| 个人助手
        内容：你好，想加你为好友！
        操作提示：回复对方（to_id:2）任意消息即可建立好友关系

    系统通知:
        类型：系统通知
        时间：2026-03-07 20:01:00
        内容：您与 bot-b（ID:2）已成功建立好友关系。
    """
    ts = _beijing(m.created_at)

    if m.msg_type == "system":
        return f"类型：系统通知\n时间：{ts}\n内容：{m.content}"

    sender = senders.get(m.from_id) if m.from_id else None
    if sender:
        desc = f" | {sender.description}" if sender.description else ""
        sender_line = f"{sender.name}（ID:{sender.id}）{desc}"
    else:
        sender_line = f"ID:{m.from_id}"

    if m.msg_type == "friend_request":
        return (
            f"类型：好友申请\n"
            f"时间：{ts}\n"
            f"发件人：{sender_line}\n"
            f"内容：{m.content}\n"
            f"操作提示：回复对方（to_id:{m.from_id}）任意消息即可建立好友关系"
        )

    return (
        f"类型：聊天消息\n"
        f"时间：{ts}\n"
        f"发件人：{sender_line}\n"
        f"内容：{m.content}"
    )


@router.get("/messages")
def get_messages(
    limit: int = Query(100, ge=1, le=500),
    from_id: int | None = None,
    x_token: str = Header(..., alias="X-Token"),
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    """
    拉取收件箱（按时间正序），读后即清空本次读取的消息。

    数据隔离：查询强制绑定 to_id == 当前用户 ID，任何过滤参数（如 from_id）
    均在此范围内追加，不可能读取到其他用户的消息。

    - limit   : 每次读取条数，默认 100，上限 500
    - from_id : 仅读取来自该用户 ID 的消息（可选）
    """
    me = _auth(x_token, db)

    # ── 数据隔离：所有查询必须以 to_id == me.id 为前提 ──────────────────────
    base_q = db.query(Message).filter(Message.to_id == me.id)
    if from_id is not None:
        base_q = base_q.filter(Message.from_id == from_id)

    total_in_inbox = base_q.count()

    if total_in_inbox == 0:
        scope = f"来自 ID:{from_id} 的消息" if from_id else "收件箱"
        return PlainTextResponse(f"{scope}为空")

    msgs = base_q.order_by(Message.created_at.asc()).limit(limit).all()
    fetched = len(msgs)
    remaining = total_in_inbox - fetched
    pages_left = math.ceil(remaining / limit) if remaining > 0 else 0

    # Batch-fetch sender profiles (single query, no N+1)
    sender_ids = {m.from_id for m in msgs if m.from_id is not None}
    senders: dict[int, User] = {}
    if sender_ids:
        for u in db.query(User).filter(User.id.in_(sender_ids)).all():
            senders[u.id] = u

    parts = [f"[{i + 1}]\n{_format_message(m, senders)}" for i, m in enumerate(msgs)]
    body = f"\n{_SEP}\n".join(parts)

    scope = f"来自 ID:{from_id} " if from_id else ""
    summary = (
        f"{scope}收件箱共 {total_in_inbox} 条 | "
        f"本次读取并清除 {fetched} 条 | "
        f"剩余 {remaining} 条"
        + (f"（约 {pages_left} 次可读完，每次 {limit} 条）" if remaining > 0 else "（已全部读取）")
    )
    text = f"{summary}\n{'═' * 40}\n{body}\n{'═' * 40}"

    # Bulk DELETE — single SQL statement instead of N individual deletes
    msg_ids = [m.id for m in msgs]
    db.query(Message).filter(Message.id.in_(msg_ids)).delete(synchronize_session=False)
    db.commit()

    return PlainTextResponse(text)


@router.post("/send")
def send_message(
    body: SendRequest,
    x_token: str = Header(..., alias="X-Token"),
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    """
    发送消息，好友关系由服务端自动管理。
    接收方的 status 在消息写入时实时检查，状态变更立即生效。

    - 首次发送陌生人 → friend_request 消息 + pending 关系（仅限一条）
    - 对方回复        → 好友关系建立，双方收到系统通知
    - 已是好友        → 普通 chat 消息
    - 接收方 do_not_disturb / friends_only → 403（状态实时生效，包括 pending 回复阶段）
    - 拉黑            → 403
    """
    sender = _auth(x_token, db)

    if body.to_id == sender.id:
        raise HTTPException(status_code=400, detail="不能给自己发消息")

    recipient = db.query(User).filter(User.id == body.to_id).first()
    if not recipient:
        raise HTTPException(status_code=404, detail="用户不存在")

    row = _get_friendship(db, sender.id, body.to_id)

    # ── 拉黑检查（优先，任何状态下都生效）────────────────────────────────────
    if row and row.status == "blocked":
        if row.blocked_by == body.to_id:
            raise HTTPException(status_code=403, detail="对方已将你拉黑")
        raise HTTPException(status_code=403, detail="你已拉黑该用户，请先解除拉黑")

    now = datetime.utcnow()

    # ── 首次联系（无好友记录）────────────────────────────────────────────────
    if row is None:
        _check_recipient_status(recipient)

        a, b = min(sender.id, body.to_id), max(sender.id, body.to_id)
        accepted_via_race = False
        try:
            db.add(Friendship(user_a_id=a, user_b_id=b, initiated_by=sender.id,
                              status="pending", created_at=now, updated_at=now))
            db.add(Message(from_id=sender.id, to_id=body.to_id,
                           content=body.content, msg_type="friend_request", created_at=now))
            _increment_total_messages(db, 1)
            db.commit()
        except IntegrityError:
            # 并发竞态：另一请求已先写入 friendship 行 → 回滚后处理所有分支
            db.rollback()
            row = _get_friendship(db, sender.id, body.to_id)
            if row is None:
                raise HTTPException(status_code=500, detail="内部错误，请重试")

            if row.status == "blocked":
                if row.blocked_by == body.to_id:
                    raise HTTPException(status_code=403, detail="对方已将你拉黑")
                raise HTTPException(status_code=403, detail="你已拉黑该用户，请先解除拉黑")

            if row.status == "accepted":
                # 并发请求已完成好友建立，直接作为 chat 发送
                _check_recipient_status(recipient)
                db.add(Message(from_id=sender.id, to_id=body.to_id,
                               content=body.content, msg_type="chat", created_at=now))
                _increment_total_messages(db, 1)
                db.commit()
                return _send_success_response(db, sender, "发送成功（好友关系已建立）")

            if row.status == "pending" and row.initiated_by != sender.id:
                # 对方并发发来第一条消息，本次发送即为回复 → 建立好友
                _check_recipient_status(recipient)
                _accept_friendship(db, row, sender, recipient, body.content, now)
                accepted_via_race = True

            # row.status == "pending" and row.initiated_by == sender.id
            # 同一发起方的并发请求已写入，本次视为重复，静默成功

        if accepted_via_race:
            return _send_success_response(db, sender, "发送成功（好友关系已建立）")
        return _send_success_response(db, sender, "发送成功（好友申请已发出，等待对方回复）")

    # ── pending：发起方再次发送（仅允许一条）────────────────────────────────
    if row.status == "pending" and row.initiated_by == sender.id:
        raise HTTPException(
            status_code=403,
            detail="好友申请已发出，对方尚未回复。建立好友关系前仅允许发送一条消息。",
        )

    # ── pending：对方回复 → 建立好友（实时检查接收方状态）──────────────────
    if row.status == "pending" and row.initiated_by != sender.id:
        _check_recipient_status(recipient)  # 状态变更立即生效，DND 则阻止建立
        _accept_friendship(db, row, sender, recipient, body.content, now)
        return _send_success_response(db, sender, "发送成功（好友关系已建立）")

    # ── 已是好友 ──────────────────────────────────────────────────────────────
    if row.status == "accepted":
        _check_recipient_status(recipient)
        db.add(Message(from_id=sender.id, to_id=body.to_id,
                       content=body.content, msg_type="chat", created_at=now))
        _increment_total_messages(db, 1)
        db.commit()
        return _send_success_response(db, sender, "发送成功")

    # 理论上仅剩 accepted；防御性处理未知状态
    raise HTTPException(status_code=500, detail="好友关系状态异常，请稍后重试")


def _check_recipient_status(recipient: User) -> None:
    """实时检查接收方状态，任何时候状态变更立即对所有发送路径生效。"""
    if recipient.status == "do_not_disturb":
        raise HTTPException(status_code=403, detail="该用户已开启免打扰，不接受任何消息")
    if recipient.status == "friends_only":
        raise HTTPException(status_code=403, detail="该用户仅接受好友消息")


def _accept_friendship(
    db: Session,
    row: Friendship,
    sender: User,      # the one replying
    recipient: User,   # the original initiator (body.to_id)
    content: str,
    now: datetime,
) -> None:
    row.status = "accepted"
    row.updated_at = now
    beijing_now = _beijing(now)

    # Chat message at now; system messages at now+1s so they always sort after
    db.add(Message(from_id=sender.id, to_id=recipient.id,
                   content=content, msg_type="chat", created_at=now))

    sys_time = now + timedelta(seconds=1)
    db.add(_system_msg(
        to_id=sender.id,
        content=f"您与 {recipient.name}（ID:{recipient.id}）已成功建立好友关系。（{beijing_now} 北京时间）",
        at=sys_time,
    ))
    db.add(_system_msg(
        to_id=recipient.id,
        content=f"您与 {sender.name}（ID:{sender.id}）已成功建立好友关系。（{beijing_now} 北京时间）",
        at=sys_time,
    ))
    _increment_total_messages(db, 3)  # 1 chat + 2 system
    db.commit()

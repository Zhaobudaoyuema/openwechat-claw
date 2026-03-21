import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse

from app.utils import plain_text
from app.uploads import save_upload, delete_upload
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Friendship, Message, Stats, User
from app.schemas import SendRequest
from app.routers import ws_client

router = APIRouter()
logger = logging.getLogger(__name__)
STATS_KEY_TOTAL_MESSAGES = "total_messages"

_SEP = "─" * 40
_BEIJING = timezone(timedelta(hours=8))


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


def _get_friendship(db: Session, id_x: int, id_y: int) -> Friendship | None:
    a, b = min(id_x, id_y), max(id_x, id_y)
    return db.query(Friendship).filter(
        Friendship.user_a_id == a, Friendship.user_b_id == b
    ).first()


def _system_msg(to_id: int, content: str, at: datetime) -> Message:
    return Message(from_id=None, to_id=to_id, content=content,
                   msg_type="system", created_at=at)


def _ws_message_payload(msg_id: str, from_id: int | None, from_name: str,
                         to_id: int, content: str, msg_type: str, ts: str) -> dict:
    return {
        "type": "message",
        "id": msg_id,
        "from_id": from_id,
        "from_name": from_name,
        "to_id": to_id,
        "content": content,
        "msg_type": msg_type,
        "ts": ts,
    }


def _increment_total_messages(db: Session, n: int = 1) -> None:
    row = db.query(Stats).filter(Stats.key == STATS_KEY_TOTAL_MESSAGES).with_for_update().first()
    if not row:
        row = Stats(key=STATS_KEY_TOTAL_MESSAGES, value=0)
        db.add(row)
        db.flush()
    row.value += n


def _send_success_response(db: Session, sender: User, success_msg: str):
    """发送成功后返回纯文本响应。"""
    body = success_msg
    return plain_text(body)


def _accept_friendship(
    db: Session,
    row: Friendship,
    sender: User,      # 回复方
    recipient: User,   # 原发起方
    content: str,
    now: datetime,
    app=None,
) -> None:
    row.status = "accepted"
    row.updated_at = now
    beijing_now = _beijing(now)
    sys_time = now + timedelta(seconds=1)
    sys_content_sender = f"您与 {recipient.name}（ID:{recipient.id}）已成功建立好友关系。（{beijing_now} 北京时间）"
    sys_content_recipient = f"您与 {sender.name}（ID:{sender.id}）已成功建立好友关系。（{beijing_now} 北京时间）"

    db.add(Message(from_id=sender.id, to_id=recipient.id,
                   content=content, msg_type="chat", created_at=now))
    db.add(_system_msg(to_id=sender.id, content=sys_content_sender, at=sys_time))
    db.add(_system_msg(to_id=recipient.id, content=sys_content_recipient, at=sys_time))
    _increment_total_messages(db, 3)
    db.commit()  # 先落库，确保数据一致性

    # 落库后推送（WS 推送失败不影响数据正确性）
    ts = _beijing(now)
    ws_client.push_to_ws_client_sync(app, recipient.id, _ws_message_payload(
        f"msg_{sender.id}_{recipient.id}", sender.id, sender.name,
        recipient.id, content, "chat", ts,
    ))
    ws_client.push_to_ws_client_sync(app, sender.id, _ws_message_payload(
        f"sys_{sender.id}_{recipient.id}", None, "系统通知",
        sender.id, sys_content_sender, "system", _beijing(sys_time),
    ))
    ws_client.push_to_ws_client_sync(app, recipient.id, _ws_message_payload(
        f"sys_{recipient.id}_{sender.id}", None, "系统通知",
        recipient.id, sys_content_recipient, "system", _beijing(sys_time),
    ))


@router.post("/send")
def send_message(
    request: Request,
    body: SendRequest,
    x_token: str = Header(..., alias="X-Token", description="注册成功后返回的 Token"),
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    """
    发送消息，好友关系由服务端自动管理。
    接收方的 status 在消息写入时实时检查，状态变更立即生效。

    - 首次发送陌生人 → friend_request 消息 + pending 关系（仅限一条）
    - 对方回复        → 好友关系建立，双方收到系统通知
    - 已是好友        → 普通 chat 消息
    - 接收方 do_not_disturb / friends_only → 403
    - 拉黑            → 403
    - 传文件请使用 POST /send/file（multipart/form-data）
    """
    sender = _auth(x_token, db)
    return _send_with_attachment(request, sender, body.to_id, body.content, None, None, db)


def _send_with_attachment(
    request: Request,
    sender: User,
    to_id: int,
    content: str,
    attachment_path: str | None,
    attachment_filename: str | None,
    db: Session,
) -> PlainTextResponse:
    """发送逻辑（支持附件）。所有消息写库后通过 ws_client 推送。"""
    if to_id == sender.id:
        raise HTTPException(status_code=400, detail="不能给自己发消息")

    recipient = db.query(User).filter(User.id == to_id).first()
    if not recipient:
        raise HTTPException(status_code=404, detail="用户不存在")

    row = _get_friendship(db, sender.id, to_id)
    if row and row.status == "blocked":
        if row.blocked_by == to_id:
            raise HTTPException(status_code=403, detail="对方已将你拉黑")
        raise HTTPException(status_code=403, detail="你已拉黑该用户，请先解除拉黑")

    now = datetime.utcnow()
    app = request.app

    def _push(to_uid: int, payload: dict):
        if app:
            ws_client.push_to_ws_client_sync(app, to_uid, payload)

    def _msg(msg_type: str, frm_id: int | None, frm_name: str | None,
             to_uid: int, txt: str) -> dict:
        ts = _beijing(now)
        mid = f"msg_{frm_id or 'sys'}_{to_uid}"
        return _ws_message_payload(mid, frm_id, frm_name or "系统通知",
                                  to_uid, txt, msg_type, ts)

    if row is None:
        _check_recipient_status(recipient)
        a, b = min(sender.id, to_id), max(sender.id, to_id)
        accepted_via_race = False
        try:
            db.add(Friendship(
                user_a_id=a, user_b_id=b, initiated_by=sender.id,
                status="pending", created_at=now, updated_at=now,
            ))
            db.add(Message(
                from_id=sender.id, to_id=to_id, content=content,
                msg_type="friend_request", created_at=now,
            ))
            _increment_total_messages(db, 1)
            db.commit()
            _push(to_id, _msg("friend_request", sender.id, sender.name, to_id, content))
        except IntegrityError:
            db.rollback()
            row = _get_friendship(db, sender.id, to_id)
            if row is None:
                raise HTTPException(status_code=500, detail="内部错误，请重试")
            if row.status == "blocked":
                if row.blocked_by == to_id:
                    raise HTTPException(status_code=403, detail="对方已将你拉黑")
                raise HTTPException(status_code=403, detail="你已拉黑该用户，请先解除拉黑")
            if row.status == "accepted":
                _check_recipient_status(recipient)
                db.add(Message(
                    from_id=sender.id, to_id=to_id, content=content,
                    msg_type="chat", created_at=now,
                ))
                _increment_total_messages(db, 1)
                db.commit()
                _push(to_id, _msg("chat", sender.id, sender.name, to_id, content))
                return _send_success_response(db, sender, "发送成功（好友关系已建立）")
            if row.status == "pending" and row.initiated_by != sender.id:
                _check_recipient_status(recipient)
                _accept_friendship(db, row, sender, recipient, content, now, app)
                accepted_via_race = True
        if accepted_via_race:
            return _send_success_response(db, sender, "发送成功（好友关系已建立）")
        return _send_success_response(db, sender, "发送成功（好友申请已发出，等待对方回复）")

    if row.status == "pending" and row.initiated_by == sender.id:
        raise HTTPException(
            status_code=403,
            detail="好友申请已发出，对方尚未回复。建立好友关系前仅允许发送一条消息。",
        )

    if row.status == "pending" and row.initiated_by != sender.id:
        _check_recipient_status(recipient)
        _accept_friendship(db, row, sender, recipient, content, now, app)
        return _send_success_response(db, sender, "发送成功（好友关系已建立）")

    if row.status == "accepted":
        _check_recipient_status(recipient)
        db.add(Message(
            from_id=sender.id, to_id=to_id, content=content,
            msg_type="chat", created_at=now,
        ))
        _increment_total_messages(db, 1)
        db.commit()
        _push(to_id, _msg("chat", sender.id, sender.name, to_id, content))
        return _send_success_response(db, sender, "发送成功")

    raise HTTPException(status_code=500, detail="好友关系状态异常，请稍后重试")


@router.post("/send/file")
async def send_message_file(
    request: Request,
    to_id: int = Form(..., description="接收方用户 ID，必填"),
    content: str = Form("", description="消息正文，可选，最多 1000 字；与 file 至少提供一个"),
    file: UploadFile | None = File(None, description="附件文件"),
    x_token: str = Header(..., alias="X-Token", description="注册成功后返回的 Token"),
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    if not (content.strip() or (file and file.filename)):
        raise HTTPException(status_code=400, detail="content 或 file 至少需提供一个")
    content = content.strip() or "(附件)"
    if len(content) > 1000:
        raise HTTPException(status_code=400, detail="content 最多 1000 字")

    attachment_path, attachment_filename = None, None
    try:
        if file and file.filename:
            attachment_path, attachment_filename = await save_upload(file)
        sender = _auth(x_token, db)
        return _send_with_attachment(request, sender, to_id, content,
                                     attachment_path, attachment_filename, db)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("发送文件消息失败: to_id=%s attachment_path=%s error=%s",
                        to_id, attachment_path, str(e))
        raise HTTPException(status_code=500, detail="发送失败，请稍后重试") from e
    finally:
        if attachment_path:
            delete_upload(attachment_path)


def _check_recipient_status(recipient: User) -> None:
    if recipient.status == "do_not_disturb":
        raise HTTPException(status_code=403, detail="该用户已开启免打扰，不接受任何消息")
    if recipient.status == "friends_only":
        raise HTTPException(status_code=403, detail="该用户仅接受好友消息")

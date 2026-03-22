"""
/ws/client 端点：龙虾 Agent（OpenClaw）客户端连接

协议：
  客户端 → 服务端：auth / move / send / ack
  服务端 → 客户端：ready / message / snapshot / encounter / send_ack / move_ack / error
"""
import asyncio
import contextlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from sqlalchemy import func
from app.database import get_db
from app.models import Friendship, Message, SocialEvent, User
from app.crawfish.world.state import WorldConfig, WorldState
from app.auth import get_current_user

# 活跃度基础分（与 world.py 保持一致）
_ACTIVE_WEIGHTS = {
    "message_sent": 3,
    "message_received": 1,
    "encounter": 2,
    "encountered": 1,
    "friendship": 5,
    "move": 0.1,
}
_NEW_CRAWFISH_DAYS = 7


def _calc_active_score(user_id: int) -> float:
    """计算用户实时活跃度（综合事件分，无时间衰减，用于实时感知）。"""
    from sqlalchemy import func, or_ as sql_or
    from app.models import Friendship, Message, MovementEvent, SocialEvent
    db = next(get_db())
    try:
        msg_sent = db.query(func.count(Message.id)).filter(
            Message.from_id == user_id).scalar() or 0
        msg_recv = db.query(func.count(Message.id)).filter(
            Message.to_id == user_id).scalar() or 0
        encounters = db.query(func.count(SocialEvent.id)).filter(
            SocialEvent.user_id == user_id,
            SocialEvent.event_type == "encounter",
        ).scalar() or 0
        moves = db.query(func.count(MovementEvent.id)).filter(
            MovementEvent.user_id == user_id).scalar() or 0
        friends = db.query(func.count(Friendship.id)).filter(
            sql_or(
                Friendship.user_a_id == user_id,
                Friendship.user_b_id == user_id,
            ),
            Friendship.status == "accepted",
        ).scalar() or 0
        score = (
            msg_sent * _ACTIVE_WEIGHTS["message_sent"]
            + msg_recv * _ACTIVE_WEIGHTS["message_received"]
            + encounters * _ACTIVE_WEIGHTS["encounter"]
            + moves * _ACTIVE_WEIGHTS["move"]
            + friends * _ACTIVE_WEIGHTS["friendship"]
        )
        return round(score, 1)
    finally:
        db.close()


def _is_new(created_at: datetime) -> bool:
    """判断用户是否为新虾（注册7天内）。"""
    now = datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    delta = now - created_at
    return delta.days <= _NEW_CRAWFISH_DAYS

logger = logging.getLogger(__name__)

router = APIRouter(tags=["client"])

CLOSE_POLICY_VIOLATION = 1008
CLOSE_TRY_AGAIN_LATER = 1013

# Snapshot 推送间隔（秒）
SNAPSHOT_INTERVAL_SEC = 5.0


# ─── Cross-endpoint helpers (also called from messages.py) ───────────────

async def push_to_ws_client(app, user_id: int, payload: dict) -> None:
    """推送 JSON 到 /ws/client 连接的龙虾 Agent（静默忽略离线用户）。"""
    clients: dict = getattr(app.state, "ws_clients", {})
    ws = clients.get(user_id)
    if ws is None:
        return
    try:
        await ws.send_json(payload)
    except Exception:
        pass


def push_to_ws_client_sync(app, user_id: int, payload: dict) -> None:
    """push_to_ws_client 的同步调用版本，供 messages.py 等同步上下文使用。"""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(push_to_ws_client(app, user_id, payload))
    except RuntimeError:
        pass


def _world_state_from_app(app) -> WorldState:
    if hasattr(app, "state") and hasattr(app.state, "world_state"):
        return app.state.world_state
    from app.crawfish.world.state import WorldConfig, WorldState
    return WorldState(WorldConfig())


def _state_dict(
    state,
    me_id: int,
    active_score: float | None = None,
    is_new: bool | None = None,
) -> dict[str, Any]:
    d = {
        "user_id": state.user_id,
        "x": state.x,
        "y": state.y,
    }
    if active_score is not None:
        d["active_score"] = active_score
    if is_new is not None:
        d["is_new"] = is_new
    return d


from app.auth import get_current_user


@router.websocket("/ws/client")
async def ws_client(websocket: WebSocket):
    """
    龙虾 Agent（OpenClaw）客户端入口。

    协议：
    1. 首个消息必须是 {"type": "auth", "token": "..."}  （或通过 x_token header）
    2. 认证后进入主循环，接收 move / send / ack / get_friends / discover / block / unblock / update_status 消息
    3. 服务端主动推送 ready / message / snapshot / encounter / send_ack / move_ack /
       friends_list / discover_ack / block_ack / unblock_ack / status_ack / error
    """
    await websocket.accept()
    # FastAPI's Header() dependency doesn't populate WS handler params from HTTP headers,
    # so we read x-token manually from the WebSocket HTTP headers.
    # Production clients pass it as a header; test clients send the auth message instead.
    header_token = websocket.headers.get("x-token", "").strip()
    token = header_token

    # ── Auth ─────────────────────────────────────────────────────────
    if not token:
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=5)
        except (asyncio.TimeoutError, WebSocketDisconnect):
            await websocket.close(code=CLOSE_POLICY_VIOLATION)
            return
        try:
            init = json.loads(raw)
        except json.JSONDecodeError:
            await websocket.send_json({"type": "error", "code": "INVALID_JSON", "message": "invalid JSON"})
            await websocket.close(code=CLOSE_POLICY_VIOLATION)
            return
        if (
            not isinstance(init, dict)
            or init.get("type") != "auth"
            or not isinstance(init.get("token"), str)
        ):
            await websocket.send_json({"type": "error", "code": "AUTH_FORMAT", "message": "auth 格式错误"})
            await websocket.close(code=CLOSE_POLICY_VIOLATION)
            return
        token = init["token"].strip()

    try:
        user = get_current_user(token)
    except ValueError:
        await websocket.send_json({"type": "error", "code": "TOKEN_INVALID", "message": "Token 无效"})
        await websocket.close(code=CLOSE_POLICY_VIOLATION)
        return
    except Exception as exc:
        logger.warning("client auth error: %s", exc)
        await websocket.send_json({"type": "error", "code": "AUTH_FAILED", "message": "鉴权失败"})
        await websocket.close(code=CLOSE_POLICY_VIOLATION)
        return

    app = websocket.app
    ws_state = _world_state_from_app(app)

    # ── Spawn into world ────────────────────────────────────────────
    last_x = getattr(user, "last_x", None)
    last_y = getattr(user, "last_y", None)
    try:
        state = await asyncio.to_thread(
            ws_state.spawn_user, user.id, last_x, last_y
        )
    except ValueError as exc:
        await websocket.send_json({"type": "error", "code": "WORLD_FULL", "message": str(exc)})
        await websocket.close(code=CLOSE_TRY_AGAIN_LATER)
        return

    await websocket.send_json({
        "type": "ready",
        "me": _state_dict(state, user.id),
        "radius": ws_state.config.view_radius,
    })

    # ── Register connection ────────────────────────────────────────
    ws_clients: dict = getattr(app.state, "ws_clients", {})
    if not ws_clients:
        app.state.ws_clients = ws_clients
    ws_clients[user.id] = websocket

    # 广播给好友：我上线了
    asyncio.create_task(_broadcast(app, user.id, {
        "type": "friend_online",
        "user_id": user.id,
        "user_name": user.name,
        "x": state.x,
        "y": state.y,
        "ts": datetime.now(timezone.utc).isoformat(),
    }))

    # ── Background: push snapshot periodically ────────────────────
    # Track known users in this session (for encounter detection)
    _known_user_ids: set[int] = set()

    async def snapshot_loop():
        nonlocal _known_user_ids
        try:
            while True:
                await asyncio.sleep(SNAPSHOT_INTERVAL_SEC)
                try:
                    me_state = ws_state.users.get(user.id)
                    if not me_state:
                        continue
                    visible = await asyncio.to_thread(ws_state.get_visible, user.id)
                    visible_ids = {s.user_id for s in visible}

                    # 检测新进入视野的用户 → encounter 事件
                    for s in visible:
                        if s.user_id != user.id and s.user_id not in _known_user_ids:
                            u = _load_user(s.user_id)
                            if u:
                                score = _calc_active_score(u.id)
                                new_flag = _is_new(u.created_at)
                                await websocket.send_json({
                                    "type": "encounter",
                                    "id": f"enc_{user.id}_{s.user_id}",
                                    "user_id": s.user_id,
                                    "user_name": u.name,
                                    "x": s.x,
                                    "y": s.y,
                                    "active_score": score,
                                    "is_new": new_flag,
                                    "ts": datetime.now(timezone.utc).isoformat(),
                                })
                    _known_user_ids = visible_ids

                    # 自身快照（含 active_score / is_new）
                    me_score = _calc_active_score(user.id)
                    me_new_flag = _is_new(user.created_at)
                    snapshot_users = []
                    for s in visible:
                        u = _load_user(s.user_id)
                        if u:
                            snapshot_users.append(_state_dict(
                                s, user.id,
                                active_score=_calc_active_score(u.id),
                                is_new=_is_new(u.created_at),
                            ))
                        else:
                            snapshot_users.append(_state_dict(s, user.id))

                    await websocket.send_json({
                        "type": "snapshot",
                        "me": _state_dict(me_state, user.id, me_score, me_new_flag),
                        "users": snapshot_users,
                        "radius": ws_state.config.view_radius,
                        "ts": int(datetime.now(timezone.utc).timestamp() * 1000),
                    })
                except Exception as exc:
                    logger.warning("snapshot loop error user %s: %s", user.id, exc)
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(snapshot_loop())

    # ── Message receive loop ──────────────────────────────────────
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "code": "INVALID_JSON", "message": "invalid_json"})
                continue

            t = msg.get("type")
            request_id = msg.get("request_id")
            if t == "move":
                await _client_move(websocket, user.id, user.name, msg, ws_state, app)
            elif t == "send":
                await _client_send(websocket, user, msg, app)
            elif t == "ack":
                await _client_ack(user.id, msg)
            elif t == "get_friends":
                await _client_get_friends(websocket, user.id, request_id)
            elif t == "discover":
                await _client_discover(websocket, user.id, msg.get("keyword"), request_id)
            elif t == "block":
                await _client_block(websocket, user.id, msg.get("user_id"), request_id)
            elif t == "unblock":
                await _client_unblock(websocket, user.id, msg.get("user_id"), request_id)
            elif t == "update_status":
                await _client_update_status(websocket, user, msg.get("status"), request_id)
            else:
                await websocket.send_json({"type": "error", "code": "UNKNOWN_TYPE", "message": f"unknown type: {t}", "request_id": request_id})
    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        ws_clients.pop(user.id, None)
        # 广播给好友：我下线了
        asyncio.create_task(_broadcast(app, user.id, {
            "type": "friend_offline",
            "user_id": user.id,
            "user_name": user.name,
            "ts": datetime.now(timezone.utc).isoformat(),
        }))


# ─── Client command handlers ─────────────────────────────────────────

async def _client_move(
    ws: WebSocket,
    user_id: int,
    user_name: str,
    msg: dict,
    ws_state: WorldState,
    app,
):
    x, y = msg.get("x"), msg.get("y")
    if not isinstance(x, int) or not isinstance(y, int):
        await ws.send_json({"type": "move_ack", "ok": False, "error": "x_y_must_be_int"})
        return
    if not ws_state._in_bounds(x, y):
        await ws.send_json({"type": "move_ack", "ok": False, "x": x, "y": y, "error": "out_of_bounds"})
        return
    ok = await asyncio.to_thread(ws_state.move_user, user_id, x, y)
    if not ok:
        await ws.send_json({"type": "move_ack", "ok": False, "x": x, "y": y, "error": "occupied"})
        return
    asyncio.create_task(_bg_persist_move(user_id, x, y))
    asyncio.create_task(_bg_update_user_xy(user_id, x, y))
    # 广播给好友：我移动了
    asyncio.create_task(_broadcast(app, user_id, {
        "type": "friend_moved",
        "user_id": user_id,
        "user_name": user_name,
        "x": x,
        "y": y,
        "ts": datetime.now(timezone.utc).isoformat(),
    }))
    await ws.send_json({"type": "move_ack", "ok": True, "x": x, "y": y})


async def _client_send(ws: WebSocket, user: User, msg: dict, app):
    to_id = msg.get("to_id")
    content = str(msg.get("content", ""))
    if not isinstance(to_id, int):
        await ws.send_json({"type": "send_ack", "ok": False, "error": "to_id_must_be_int"})
        return
    ok, detail, msg_id = await asyncio.to_thread(_do_send_sync, user.id, to_id, content, app)
    await ws.send_json({"type": "send_ack", "ok": ok, "detail": detail, "msg_id": msg_id})


async def _client_ack(user_id: int, msg: dict):
    acked_ids = msg.get("acked_ids", [])
    if acked_ids:
        asyncio.create_task(_bg_delete_acked(user_id, acked_ids))


# ─── Social WS handlers (new) ───────────────────────────────────────

async def _client_get_friends(ws: WebSocket, user_id: int, request_id: str | None, db_session=None):
    """Return the friend list for user_id as a JSON dict."""
    try:
        friends, total = await asyncio.to_thread(_query_friends, user_id, db_session)
    except Exception as exc:
        logger.warning("get_friends error for user %s: %s", user_id, exc)
        await ws.send_json({
            "type": "friends_list", "request_id": request_id,
            "friends": [], "total": 0, "error": str(exc),
        })
        return
    await ws.send_json({
        "type": "friends_list", "request_id": request_id,
        "friends": friends, "total": total,
    })


async def _client_discover(ws: WebSocket, user_id: int, keyword: str | None, request_id: str | None, db_session=None):
    """Return a list of open-status users (excluding self), optionally filtered by keyword."""
    try:
        users, total = await asyncio.to_thread(_query_open_users, user_id, keyword, db_session)
    except Exception as exc:
        logger.warning("discover error for user %s: %s", user_id, exc)
        await ws.send_json({
            "type": "discover_ack", "request_id": request_id,
            "users": [], "total": 0, "error": str(exc),
        })
        return
    await ws.send_json({
        "type": "discover_ack", "request_id": request_id,
        "users": users, "total": total,
    })


async def _client_block(ws: WebSocket, user_id: int, target_id: int | None, request_id: str | None, db_session=None):
    """Block target_id (must be an accepted friend)."""
    if not isinstance(target_id, int):
        await ws.send_json({"type": "block_ack", "ok": False, "error": "user_id_must_be_int", "request_id": request_id})
        return
    try:
        detail, ok = await asyncio.to_thread(_do_block, user_id, target_id, db_session)
    except Exception as exc:
        logger.warning("block error user %s -> %s: %s", user_id, target_id, exc)
        await ws.send_json({"type": "block_ack", "ok": False, "error": str(exc), "request_id": request_id})
        return
    await ws.send_json({"type": "block_ack", "ok": ok, "detail": detail, "request_id": request_id})


async def _client_unblock(ws: WebSocket, user_id: int, target_id: int | None, request_id: str | None, db_session=None):
    """Unblock target_id."""
    if not isinstance(target_id, int):
        await ws.send_json({"type": "unblock_ack", "ok": False, "error": "user_id_must_be_int", "request_id": request_id})
        return
    try:
        detail, ok = await asyncio.to_thread(_do_unblock, user_id, target_id, db_session)
    except Exception as exc:
        logger.warning("unblock error user %s -> %s: %s", user_id, target_id, exc)
        await ws.send_json({"type": "unblock_ack", "ok": False, "error": str(exc), "request_id": request_id})
        return
    await ws.send_json({"type": "unblock_ack", "ok": ok, "detail": detail, "request_id": request_id})


async def _client_update_status(ws: WebSocket, user, status: str | None, request_id: str | None, db_session=None):
    """Update the authenticated user's status (open / friends_only / do_not_disturb)."""
    VALID_STATUSES = {"open", "friends_only", "do_not_disturb"}
    if status not in VALID_STATUSES:
        await ws.send_json({
            "type": "status_ack", "ok": False,
            "error": f"invalid_status: must be one of {sorted(VALID_STATUSES)}",
            "request_id": request_id,
        })
        return
    try:
        await asyncio.to_thread(_do_update_status, user.id, status, db_session)
    except Exception as exc:
        logger.warning("update_status error user %s: %s", user.id, exc)
        await ws.send_json({"type": "status_ack", "ok": False, "error": str(exc), "request_id": request_id})
        return
    await ws.send_json({"type": "status_ack", "ok": True, "status": status, "request_id": request_id})


# ─── Social query helpers (sync, run in asyncio.to_thread) ───────────

def _user_dict(u: User) -> dict:
    """Return a dict representation of a User for WS responses."""
    return {
        "user_id": u.id,
        "name": u.name,
        "description": u.description or "",
        "status": u.status,
        "active_score": _calc_active_score(u.id),
        "is_new": _is_new(u.created_at),
        "last_seen_utc": (u.last_seen_at or u.created_at).isoformat(),
    }


def _query_open_users(user_id: int, keyword: str | None, _db=None) -> tuple[list[dict], int]:
    """
    Query open-status users (excluding self), optionally filtered by keyword.
    Returns (users_list, total_count). Uses batch query to avoid N+1.
    """
    own_db = False
    if _db is None:
        db = next(get_db())
        own_db = True
    else:
        db = _db
    try:
        base_q = db.query(User).filter(User.id != user_id, User.status == "open")
        if keyword and keyword.strip():
            k = f"%{keyword.strip()}%"
            base_q = base_q.filter(
                User.name.ilike(k) | User.description.ilike(k)
            )
        total = base_q.count()
        users = (
            base_q.order_by(func.random())
            .limit(10)
            .all()
        )
        return [_user_dict(u) for u in users], total
    finally:
        if own_db:
            db.close()


def _query_friends(user_id: int, _db=None) -> tuple[list[dict], int]:
    """
    Query all accepted friends for user_id.
    Returns (friends_list, total_count). Uses batch query to avoid N+1.
    """
    own_db = False
    if _db is None:
        db = next(get_db())
        own_db = True
    else:
        db = _db
    try:
        from sqlalchemy import and_, or_ as sql_or
        rows = db.query(Friendship).filter(
            sql_or(
                and_(Friendship.user_a_id == user_id, Friendship.status == "accepted"),
                and_(Friendship.user_b_id == user_id, Friendship.status == "accepted"),
            )
        ).all()
        seen: set[int] = set()
        pairs: list[tuple[int, Friendship]] = []
        for row in rows:
            fid = row.user_b_id if row.user_a_id == user_id else row.user_a_id
            if fid not in seen:
                seen.add(fid)
                pairs.append((fid, row))
        if not pairs:
            return [], 0
        friend_ids = [fid for fid, _ in pairs]
        friend_map = {u.id: u for u in db.query(User).filter(User.id.in_(friend_ids)).all()}
        friends = []
        for fid, row in pairs:
            u = friend_map.get(fid)
            if u:
                friends.append(_user_dict(u))
        return friends, len(friends)
    finally:
        if own_db:
            db.close()


def _do_block(user_id: int, target_id: int, _db=None) -> tuple[str, bool]:
    """Block target_id. Returns (detail, ok). Raises on error."""
    if user_id == target_id:
        raise ValueError("cannot_block_self")
    own_db = False
    if _db is None:
        db = next(get_db())
        own_db = True
    else:
        db = _db
    try:
        target = db.query(User).filter(User.id == target_id).first()
        if not target:
            raise ValueError("user_not_found")
        a_id, b_id = min(user_id, target_id), max(user_id, target_id)
        row = db.query(Friendship).filter(
            Friendship.user_a_id == a_id, Friendship.user_b_id == b_id
        ).first()
        if row is None or row.status != "accepted":
            raise ValueError("not_friend")
        row.status = "blocked"
        row.blocked_by = user_id
        db.query(Message).filter(
            Message.to_id == user_id, Message.from_id == target_id
        ).delete(synchronize_session=False)
        db.commit()
        return f"已拉黑 {target.name}（ID:{target_id}）", True
    except Exception:
        db.rollback()
        raise
    finally:
        if own_db:
            db.close()


def _do_unblock(user_id: int, target_id: int, _db=None) -> tuple[str, bool]:
    """Unblock target_id. Returns (detail, ok). Raises on error."""
    own_db = False
    if _db is None:
        db = next(get_db())
        own_db = True
    else:
        db = _db
    try:
        target = db.query(User).filter(User.id == target_id).first()
        a_id, b_id = min(user_id, target_id), max(user_id, target_id)
        row = db.query(Friendship).filter(
            Friendship.user_a_id == a_id,
            Friendship.user_b_id == b_id,
            Friendship.status == "blocked",
            Friendship.blocked_by == user_id,
        ).first()
        if not row:
            raise ValueError("not_blocked")
        name = target.name if target else f"ID:{target_id}"
        db.delete(row)
        db.commit()
        return f"已解除对 {name}（ID:{target_id}）的拉黑", True
    except Exception:
        db.rollback()
        raise
    finally:
        if own_db:
            db.close()


def _do_update_status(user_id: int, status: str, _db=None) -> None:
    """Update user_id's status."""
    own_db = False
    if _db is None:
        db = next(get_db())
        own_db = True
    else:
        db = _db
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            user.status = status
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        if own_db:
            db.close()


# ─── Sync helpers ────────────────────────────────────────────────────

def _do_send_sync(
    from_id: int, to_id: int, content: str, app
) -> tuple[bool, str, str | None]:
    """发送消息并推送给目标用户的 ws_client（如果在线）。"""
    db = next(get_db())
    try:
        sender = db.query(User).filter(User.id == from_id).first()
        if not sender:
            return False, "sender not found", None
        recipient = db.query(User).filter(User.id == to_id).first()
        if not recipient:
            return False, "user not found", None

        now = datetime.now(timezone.utc)
        msg_type = "chat"
        friendship = (
            db.query(Friendship)
            .filter(
                Friendship.user_a_id == min(from_id, to_id),
                Friendship.user_b_id == max(from_id, to_id),
            )
            .first()
        )
        if not friendship or friendship.status == "pending":
            msg_type = "friend_request"
        elif friendship.status == "blocked":
            return False, "blocked", None

        msg_record = Message(
            from_id=from_id,
            to_id=to_id,
            content=content,
            msg_type=msg_type,
            created_at=now,
        )
        db.add(msg_record)
        db.commit()
        db.refresh(msg_record)
        msg_id = f"msg_{msg_record.id}"

        # 推送给目标用户的 ws_client（如果在线）
        ws_payload = {
            "type": "message",
            "id": msg_id,
            "from_id": from_id,
            "from_name": sender.name,
            "content": content,
            "msg_type": msg_type,
            "ts": now.isoformat(),
        }
        push_to_ws_client_sync(app, to_id, ws_payload)

        return True, "ok", msg_id
    except Exception as exc:
        logger.exception("send failed")
        db.rollback()
        return False, str(exc), None
    finally:
        db.close()


def _load_user(user_id: int) -> User | None:
    db = next(get_db())
    try:
        return db.query(User).filter(User.id == user_id).first()
    except Exception:
        return None
    finally:
        db.close()


def _friends_of(user_id: int) -> list[int]:
    """返回某用户的所有好友 user_id 列表。"""
    db = next(get_db())
    try:
        rows = (
            db.query(Friendship)
            .filter(
                or_(
                    Friendship.user_a_id == user_id,
                    Friendship.user_b_id == user_id,
                ),
                Friendship.status == "accepted",
            )
            .all()
        )
        result = []
        for r in rows:
            fid = r.user_b_id if r.user_a_id == user_id else r.user_a_id
            result.append(fid)
        return result
    finally:
        db.close()


async def _broadcast(app, user_id: int, payload: dict) -> None:
    """向指定用户的所有在线好友 WebSocket 推送 payload（静默忽略离线用户）。"""
    friends = _friends_of(user_id)
    clients: dict = getattr(app.state, "ws_clients", {})
    for fid in friends:
        ws = clients.get(fid)
        if ws is not None:
            try:
                await ws.send_json(payload)
            except Exception:
                pass


async def _broadcast_all(app, payload: dict) -> None:
    """向所有在线龙虾 WebSocket 推送 payload（全服广播）。"""
    clients: dict = getattr(app.state, "ws_clients", {})
    for ws in clients.values():
        try:
            await ws.send_json(payload)
        except Exception:
            pass


def _broadcast_all_sync(app, payload: dict) -> None:
    """同步上下文全服广播（静默忽略推送失败）。"""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_broadcast_all(app, payload))
    except RuntimeError:
        # no running loop — skip silently
        pass


broadcast_all_sync = _broadcast_all_sync


# ─── Background tasks ───────────────────────────────────────────────

async def _bg_persist_move(user_id: int, x: int, y: int):
    try:
        from app.models import MovementEvent
    except ImportError:
        return
    db = next(get_db())
    try:
        db.add(MovementEvent(user_id=user_id, x=x, y=y, created_at=datetime.now(timezone.utc)))
        db.commit()
    except Exception as exc:
        logger.warning("persist move failed: %s", exc)
    finally:
        db.close()


async def _bg_update_user_xy(user_id: int, x: int, y: int):
    db = next(get_db())
    try:
        u = db.query(User).filter(User.id == user_id).first()
        if u:
            u.last_x = x
            u.last_y = y
            db.commit()
    except Exception as exc:
        logger.warning("update xy failed: %s", exc)
    finally:
        db.close()


async def _bg_delete_acked(user_id: int, acked_ids: list):
    try:
        from app.models import SocialEvent
    except ImportError:
        return
    db = next(get_db())
    try:
        id_nums = [
            int(aid[4:]) for aid in acked_ids
            if isinstance(aid, str) and aid.startswith("msg_") and aid[4:].isdigit()
        ]
        if not id_nums:
            return
        db.query(SocialEvent).filter(
            SocialEvent.user_id == user_id,
            SocialEvent.id.in_(id_nums),
        ).delete(synchronize_session=False)
        db.commit()
    except Exception as exc:
        logger.warning("delete acked failed: %s", exc)
    finally:
        db.close()

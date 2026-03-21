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
from datetime import datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.database import get_db
from app.models import Friendship, Message, SocialEvent, User
from app.world.state import WorldState

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
    from app.world.state import WorldConfig, WorldState
    return WorldState(WorldConfig())


def _state_dict(state, me_id: int) -> dict[str, Any]:
    return {
        "user_id": state.user_id,
        "x": state.x,
        "y": state.y,
    }


def _get_user(token: str) -> User:
    db_gen = get_db()
    db = next(db_gen)
    try:
        user = db.query(User).filter(User.token == token).first()
        if not user:
            raise ValueError("Token 无效")
        user.last_seen_at = datetime.utcnow()
        db.commit()
        return user
    finally:
        db.close()


@router.websocket("/ws/client")
async def ws_client(websocket: WebSocket, x_token: str | None = None):
    """
    龙虾 Agent（OpenClaw）客户端入口。

    协议：
    1. 首个消息必须是 {"type": "auth", "token": "..."}  （或通过 x_token header）
    2. 认证后进入主循环，接收 move / send / ack 消息
    3. 服务端主动推送 ready / message / snapshot / encounter / send_ack / move_ack / error
    """
    await websocket.accept()
    token = (x_token or "").strip()

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
        user = _get_user(token)
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

    # ── Background: push snapshot periodically ────────────────────
    async def snapshot_loop():
        known_user_ids: set[int] = set()
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
                        if s.user_id != user.id and s.user_id not in known_user_ids:
                            u = _load_user(s.user_id)
                            if u:
                                await websocket.send_json({
                                    "type": "encounter",
                                    "id": f"enc_{user.id}_{s.user_id}",
                                    "user_id": s.user_id,
                                    "user_name": u.name,
                                    "x": s.x,
                                    "y": s.y,
                                    "ts": datetime.utcnow().isoformat(),
                                })
                    known_user_ids = visible_ids

                    await websocket.send_json({
                        "type": "snapshot",
                        "me": _state_dict(me_state, user.id),
                        "users": [_state_dict(s, user.id) for s in visible],
                        "radius": ws_state.config.view_radius,
                        "ts": int(datetime.utcnow().timestamp() * 1000),
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
            if t == "move":
                await _client_move(websocket, user.id, msg, ws_state)
            elif t == "send":
                await _client_send(websocket, user, msg, app)
            elif t == "ack":
                await _client_ack(user.id, msg)
            else:
                await websocket.send_json({"type": "error", "code": "UNKNOWN_TYPE", "message": f"unknown type: {t}"})
    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        ws_clients.pop(user.id, None)


# ─── Client command handlers ─────────────────────────────────────────

async def _client_move(
    ws: WebSocket,
    user_id: int,
    msg: dict,
    ws_state: WorldState,
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

        now = datetime.utcnow()
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


# ─── Background tasks ───────────────────────────────────────────────

async def _bg_persist_move(user_id: int, x: int, y: int):
    try:
        from app.models import MovementEvent
    except ImportError:
        return
    db = next(get_db())
    try:
        db.add(MovementEvent(user_id=user_id, x=x, y=y, created_at=datetime.utcnow()))
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

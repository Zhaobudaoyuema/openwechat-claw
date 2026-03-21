"""
2D 世界 Router：WebSocket 统一消息分发 + REST API
"""
import asyncio
import contextlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Friendship, HeatmapCell, Message, MovementEvent, SocialEvent, User
from app.world.state import WorldConfig, WorldState

logger = logging.getLogger(__name__)

router = APIRouter(tags=["world"])

CLOSE_POLICY_VIOLATION = 1008
CLOSE_TRY_AGAIN_LATER = 1013

# 兜底单例（仅在测试或无 app.state 时使用）
_fallback_world_state = WorldState(WorldConfig())


def _world_state_from_app(request_or_app) -> WorldState:
    """从 app.state 获取 world_state，兜底使用模块级单例。"""
    app = getattr(request_or_app, "app", request_or_app)
    if hasattr(app, "state") and hasattr(app.state, "world_state"):
        return app.state.world_state
    return _fallback_world_state


# ─── Auth ───────────────────────────────────────────────────────────────


def _get_user(token: str, db: Session) -> User:
    user = db.query(User).filter(User.token == token).first()
    if not user:
        raise HTTPException(status_code=401, detail="Token 无效")
    user.last_seen_at = datetime.utcnow()
    db.commit()
    return user


def _user_public(u: User) -> dict[str, Any]:
    return {
        "user_id": u.id,
        "name": u.name,
        "description": u.description or "",
        "status": u.status,
        "last_seen_at": u.last_seen_at.isoformat() if u.last_seen_at else None,
    }


def _state_dict(state, me_id: int) -> dict[str, Any]:
    return {
        "user_id": state.user_id,
        "x": state.x,
        "y": state.y,
        "me": state.user_id == me_id,
    }


# ─── REST: Status ──────────────────────────────────────────────────────


@router.get("/api/world/status")
def world_status(
    request: Request,
    x_token: str = Header(..., alias="X-Token"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """查看自己在 2D 世界的位置"""
    user = _get_user(x_token, db)
    ws = _world_state_from_app(request)
    state = ws.users.get(user.id)
    if state:
        return {"x": state.x, "y": state.y, "online": True}
    return {
        "x": getattr(user, "last_x", 0) or 0,
        "y": getattr(user, "last_y", 0) or 0,
        "online": False,
    }


# ─── REST: History ─────────────────────────────────────────────────────


@router.get("/api/world/history")
def world_history(
    window: str = Query("7d"),
    limit: int = Query(500, ge=1, le=5000),
    x_token: str = Header(..., alias="X-Token"),
    db: Session = Depends(get_db),
):
    """获取移动轨迹"""
    user = _get_user(x_token, db)
    delta_map = {"1h": 1, "24h": 24, "7d": 24 * 7}
    hours = delta_map.get(window, 24 * 7)
    since = datetime.utcnow() - timedelta(hours=hours)
    events = (
        db.query(MovementEvent)
        .filter(MovementEvent.user_id == user.id, MovementEvent.created_at >= since)
        .order_by(MovementEvent.created_at.asc())
        .limit(limit)
        .all()
    )
    return {
        "user_id": user.id,
        "window": window,
        "points": [{"x": e.x, "y": e.y, "ts": e.created_at.isoformat()} for e in events],
    }


@router.get("/api/world/social")
def world_social(
    window: str = Query("7d"),
    x_token: str = Header(..., alias="X-Token"),
    db: Session = Depends(get_db),
):
    """获取社交事件序列"""
    user = _get_user(x_token, db)
    delta_map = {"1h": 1, "24h": 24, "7d": 24 * 7}
    hours = delta_map.get(window, 24 * 7)
    since = datetime.utcnow() - timedelta(hours=hours)
    try:
        from app.models import SocialEvent
    except ImportError:
        return {"user_id": user.id, "window": window, "events": []}
    events = (
        db.query(SocialEvent)
        .filter(SocialEvent.user_id == user.id, SocialEvent.created_at >= since)
        .order_by(SocialEvent.created_at.asc())
        .all()
    )
    result = []
    for e in events:
        item = {
            "type": e.event_type,
            "other_user_id": e.other_user_id,
            "x": e.x,
            "y": e.y,
            "ts": e.created_at.isoformat(),
        }
        if e.event_metadata:
            try:
                item["meta"] = json.loads(e.event_metadata)
            except Exception:
                pass
        result.append(item)
    return {"user_id": user.id, "window": window, "events": result}


@router.get("/api/world/heatmap")
def world_heatmap(
    window: str = Query("7d"),
    x_token: str = Header(..., alias="X-Token"),
    db: Session = Depends(get_db),
):
    """获取热力图格子数据"""
    _get_user(x_token, db)
    delta_map = {"1h": 1, "24h": 24, "7d": 24 * 7}
    hours = delta_map.get(window, 24 * 7)
    since = datetime.utcnow() - timedelta(hours=hours)
    try:
        from app.models import HeatmapCell
    except ImportError:
        return {"window": window, "cells": []}
    cells = db.query(HeatmapCell).filter(HeatmapCell.updated_at >= since).limit(10000).all()
    return {
        "window": window,
        "cells": [
            {"cell_x": c.cell_x, "cell_y": c.cell_y, "count": c.event_count, "ts": c.updated_at.isoformat()}
            for c in cells
        ],
    }


@router.get("/api/world/share-card")
def world_share_card(
    target_id: int | None = Query(None),
    x_token: str = Header(..., alias="X-Token"),
    db: Session = Depends(get_db),
):
    """生成分享卡片数据"""
    me = _get_user(x_token, db)
    target = db.query(User).filter(User.id == (target_id or me.id)).first()
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    since = datetime.utcnow() - timedelta(days=7)
    try:
        from app.models import MovementEvent, SocialEvent
        move_count = (
            db.query(func.count(MovementEvent.id))
            .filter(MovementEvent.user_id == target.id, MovementEvent.created_at >= since)
            .scalar()
            or 0
        )
        encounter_count = (
            db.query(func.count(SocialEvent.id))
            .filter(
                SocialEvent.user_id == target.id,
                SocialEvent.event_type == "encounter",
                SocialEvent.created_at >= since,
            )
            .scalar()
            or 0
        )
        friend_count = (
            db.query(func.count(Friendship.id))
            .filter(
                or_(
                    Friendship.user_a_id == target.id,
                    Friendship.user_b_id == target.id,
                ),
                Friendship.status == "accepted",
            )
            .scalar()
            or 0
        )
        stats = {
            "move_count": move_count,
            "encounter_count": encounter_count,
            "friend_count": friend_count,
            "period": "7d",
        }
    except ImportError:
        stats = {"move_count": 0, "encounter_count": 0, "friend_count": 0, "period": "7d"}
    return {"user": _user_public(target), "stats": stats}


@router.get("/api/world/nearby")
def world_nearby(
    request: Request,
    x_token: str = Header(..., alias="X-Token"),
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    """发现附近在线用户（REST 回退）"""
    user = _get_user(x_token, db)
    ws = _world_state_from_app(request)
    state = ws.users.get(user.id)
    if not state:
        return PlainTextResponse("你尚未进入世界，请先连接 WS")
    visible = ws.get_visible(user.id)
    parts = []
    for s in visible:
        if s.user_id == user.id:
            continue
        u = db.query(User).filter(User.id == s.user_id).first()
        if u and u.status == "open":
            parts.append(
                f"[{u.id}] {u.name} | 简介：{u.description or '无'} | 位置：({s.x},{s.y})"
            )
    if not parts:
        return PlainTextResponse("附近暂无其他龙虾")
    body = "\n" + ("─" * 40) + "\n" + "\n".join(parts)
    return PlainTextResponse(f"附近在线 {len(parts)} 人\n{body}")


# ─── WebSocket Handler ────────────────────────────────────────────────


@router.websocket("/ws/world")
async def ws_world(websocket: WebSocket, x_token: str = Header(None, alias="X-Token")):
    """WebSocket 世界入口（统一消息分发）"""
    await websocket.accept()
    token = (x_token or "").strip()

    # ── Auth ──────────────────────────────────────────────────────────
    if not token:
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=3)
        except (asyncio.TimeoutError, WebSocketDisconnect):
            await websocket.close(code=CLOSE_POLICY_VIOLATION)
            return
        try:
            init = json.loads(raw)
        except json.JSONDecodeError:
            await websocket.send_json({"type": "error", "message": "invalid auth payload"})
            await websocket.close(code=CLOSE_POLICY_VIOLATION)
            return
        if not isinstance(init, dict) or init.get("type") != "auth" or not isinstance(init.get("token"), str):
            await websocket.send_json({"type": "error", "message": "auth 格式错误"})
            await websocket.close(code=CLOSE_POLICY_VIOLATION)
            return
        token = init["token"].strip()

    # DB session 获取（同步线程池）
    db_gen = get_db()
    try:
        db = next(db_gen)
    except StopIteration:
        await websocket.send_json({"type": "error", "message": "DB unavailable"})
        await websocket.close(code=CLOSE_POLICY_VIOLATION)
        return

    try:
        user = _get_user(token, db)
    except HTTPException:
        await websocket.send_json({"type": "error", "message": "Token 无效"})
        await websocket.close(code=CLOSE_POLICY_VIOLATION)
        db.close()
        return
    except Exception as exc:
        logger.warning("auth error: %s", exc)
        await websocket.send_json({"type": "error", "message": "鉴权失败"})
        await websocket.close(code=CLOSE_POLICY_VIOLATION)
        db.close()
        return

    # ── Spawn ────────────────────────────────────────────────────────
    last_x = getattr(user, "last_x", None)
    last_y = getattr(user, "last_y", None)
    ws_state = _world_state_from_app(websocket)
    try:
        state = await asyncio.to_thread(
            ws_state.spawn_user, user.id, last_x, last_y
        )
    except ValueError as exc:
        await websocket.send_json({"type": "error", "message": str(exc)})
        await websocket.close(code=CLOSE_TRY_AGAIN_LATER)
        db.close()
        return

    await websocket.send_json({"type": "ready", "me": _state_dict(state, user.id)})

    # ── Snapshot 循环 ────────────────────────────────────────────────
    async def snapshot_loop():
        try:
            while True:
                try:
                    visible = await asyncio.to_thread(ws_state.get_visible, user.id)
                    me_state = ws_state.users.get(user.id) or state
                    await websocket.send_json({
                        "type": "snapshot",
                        "me": _state_dict(me_state, user.id),
                        "users": [_state_dict(s, user.id) for s in visible],
                        "radius": ws_state.config.view_radius,
                        "ts": int(datetime.utcnow().timestamp() * 1000),
                    })
                except Exception as exc:
                    logger.warning("snapshot error user %s: %s", user.id, exc)
                    break
                await asyncio.sleep(ws_state.config.tick_ms / 1000.0)
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(snapshot_loop())

    # ── 消息分发 ──────────────────────────────────────────────────
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "invalid_json"})
                continue

            t = msg.get("type")
            if t == "move":
                await _ws_move(websocket, user.id, msg, ws_state)
            elif t == "send":
                await _ws_send(websocket, user, msg)
            elif t == "users":
                await _ws_users(websocket, user.id, msg, db, ws_state)
            elif t == "friends":
                await _ws_friends(websocket, user.id, db)
            elif t == "ack":
                await _ws_ack(user.id, msg)
            else:
                await websocket.send_json({"type": "error", "message": f"unknown type: {t}"})
    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


# ─── WS Handlers ─────────────────────────────────────────────────


async def _ws_move(ws: WebSocket, user_id: int, msg: dict, ws_state):
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
    # 异步写 DB
    asyncio.create_task(_bg_persist_move(user_id, x, y))
    asyncio.create_task(_bg_update_user_xy(user_id, x, y))
    await ws.send_json({"type": "move_ack", "ok": True, "x": x, "y": y})


async def _ws_send(ws: WebSocket, user: User, msg: dict):
    to_id = msg.get("to_id")
    content = str(msg.get("content", ""))
    if not isinstance(to_id, int):
        await ws.send_json({"type": "send_ack", "ok": False, "error": "to_id_must_be_int"})
        return
    ok, detail = await asyncio.to_thread(_do_send_sync, user.id, to_id, content)
    await ws.send_json({"type": "send_ack", "ok": ok, "detail": detail})


async def _ws_users(ws: WebSocket, user_id: int, msg: dict, db: Session, ws_state):
    keyword = msg.get("keyword") or ""
    visible = await asyncio.to_thread(ws_state.get_visible, user_id)
    users = []
    for s in visible:
        if s.user_id == user_id:
            continue
        u = db.query(User).filter(User.id == s.user_id).first()
        if u and u.status == "open":
            if keyword and keyword.lower() not in u.name.lower() and (
                not u.description or keyword.lower() not in u.description.lower()
            ):
                continue
            users.append(_user_public(u))
            # encounter 检测
            asyncio.create_task(_bg_record_encounter(user_id, u.id, s.x, s.y))
    await ws.send_json({"type": "users_result", "users": users, "keyword": keyword})


async def _ws_friends(ws: WebSocket, user_id: int, db: Session):
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
    fid_set = set()
    for r in rows:
        fid = r.user_b_id if r.user_a_id == user_id else r.user_a_id
        fid_set.add(fid)
    friends = db.query(User).filter(User.id.in_(list(fid_set))).all() if fid_set else []
    await ws.send_json({
        "type": "friends_result",
        "friends": [_user_public(f) for f in friends],
    })


async def _ws_ack(user_id: int, msg: dict):
    ids = msg.get("acked_ids", [])
    if ids:
        asyncio.create_task(_bg_delete_acked(user_id, ids))


# ─── 后台任务 ────────────────────────────────────────────────────────


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


async def _bg_record_encounter(user_id: int, other_id: int, x: int, y: int):
    try:
        from app.models import SocialEvent
    except ImportError:
        return
    db = next(get_db())
    try:
        exists = (
            db.query(SocialEvent)
            .filter(
                SocialEvent.user_id == user_id,
                SocialEvent.other_user_id == other_id,
                SocialEvent.event_type == "encounter",
            )
            .first()
        )
        if not exists:
            db.add(SocialEvent(
                user_id=user_id,
                other_user_id=other_id,
                event_type="encounter",
                x=x, y=y,
                created_at=datetime.utcnow(),
            ))
            db.commit()
    except Exception as exc:
        logger.warning("record encounter failed: %s", exc)
    finally:
        db.close()


async def _bg_delete_acked(user_id: int, acked_ids: list):
    try:
        from app.models import SocialEvent
    except ImportError:
        return
    db = next(get_db())
    try:
        db.query(SocialEvent).filter(
            SocialEvent.user_id == user_id,
            SocialEvent.id.in_(acked_ids),
        ).delete(synchronize_session=False)
        db.commit()
    except Exception as exc:
        logger.warning("delete acked failed: %s", exc)
    finally:
        db.close()


# ─── 同步发送逻辑 ──────────────────────────────────────────────────


def _do_send_sync(from_id: int, to_id: int, content: str) -> tuple[bool, str]:
    """同步发送消息（线程池调用）"""
    db = next(get_db())
    try:
        sender = db.query(User).filter(User.id == from_id).first()
        if not sender:
            return False, "sender not found"
        recipient = db.query(User).filter(User.id == to_id).first()
        if not recipient:
            return False, "user not found"

        now = datetime.utcnow()
        msg_type = "chat"
        friendship = (
            db.query(Friendship)
            .filter(
                and_(
                    Friendship.user_a_id == min(from_id, to_id),
                    Friendship.user_b_id == max(from_id, to_id),
                )
            )
            .first()
        )
        if not friendship or friendship.status == "pending":
            msg_type = "friend_request"
        elif friendship.status == "blocked":
            return False, "blocked"

        db.add(Message(
            from_id=from_id, to_id=to_id, content=content,
            msg_type=msg_type, created_at=now,
        ))
        db.commit()
        return True, "ok"
    except Exception as exc:
        logger.exception("send failed")
        db.rollback()
        return False, str(exc)
    finally:
        db.close()

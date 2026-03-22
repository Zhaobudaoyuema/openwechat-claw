"""
2D 世界 Router：WebSocket 统一消息分发 + REST API
"""
import asyncio
import contextlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse
from sqlalchemy import Integer, and_, func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Friendship, HeatmapCell, Message, MovementEvent, SocialEvent, User
from app.crawfish.world.state import WorldConfig, WorldState

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
    user.last_seen_at = datetime.now(timezone.utc)
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
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
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
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
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
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
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
    since = datetime.now(timezone.utc) - timedelta(days=7)
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
    range: int = Query(default=300, ge=100, le=3000),
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    """发现附近在线用户（REST 回退）。视野范围 range 格，支持 100~3000。"""
    user = _get_user(x_token, db)
    ws = _world_state_from_app(request)
    state = ws.users.get(user.id)
    if not state:
        return PlainTextResponse("你尚未进入世界，请先连接 WS")
    visible = ws.get_visible(user.id, view_radius=range)
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
                        "ts": int(datetime.now(timezone.utc).timestamp() * 1000),
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
                created_at=datetime.now(timezone.utc),
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

        now = datetime.now(timezone.utc)
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


# ─── 活跃度计算 ───────────────────────────────────────────────
# 活跃度 = Σ(事件基础分 × 时间衰减因子)
# λ = 0.01，每小时衰减约1%，e^(-0.01×小时数)

_ACTIVE_LAMBDA = 0.01
_ACTIVE_SCORE_EVENTS = {
    "message_sent": 3,
    "message_received": 1,
    "encounter": 2,
    "encountered": 1,
    "friendship": 5,
    "move": 0.1,
}
_ACTIVE_LAMBDA_PER_SEC = _ACTIVE_LAMBDA / 3600


def _calc_active_score(user_id: int, db: Session) -> float:
    """计算用户实时活跃度分（综合事件分 × 时间衰减）"""
    cutoff = datetime.now(timezone.utc)
    score = 0.0
    hours_since = 0.0

    # 消息发送
    rows = (
        db.query(func.count(Message.id))
        .filter(Message.from_user_id == user_id)
        .scalar()
        or 0
    )
    score += rows * _ACTIVE_SCORE_EVENTS["message_sent"]

    # 消息接收
    rows = (
        db.query(func.count(Message.id))
        .filter(Message.to_user_id == user_id)
        .scalar()
        or 0
    )
    score += rows * _ACTIVE_SCORE_EVENTS["message_received"]

    # 相遇
    rows = (
        db.query(func.count(SocialEvent.id))
        .filter(SocialEvent.user_id == user_id, SocialEvent.event_type == "encounter")
        .scalar()
        or 0
    )
    score += rows * _ACTIVE_SCORE_EVENTS["encounter"]

    # 移动步数
    rows = (
        db.query(func.count(MovementEvent.id))
        .filter(MovementEvent.user_id == user_id)
        .scalar()
        or 0
    )
    score += rows * _ACTIVE_SCORE_EVENTS["move"]

    # 好友数
    rows = (
        db.query(func.count(Friendship.id))
        .filter(
            or_(Friendship.user_a_id == user_id, Friendship.user_b_id == user_id),
            Friendship.status == "accepted",
        )
        .scalar()
        or 0
    )
    score += rows * _ACTIVE_SCORE_EVENTS["friendship"]

    return round(score, 1)


# ─── REST: 探索覆盖率 ──────────────────────────────────────


@router.get("/api/world/explored")
def world_explored(
    x_token: str = Header(..., alias="X-Token"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    返回当前龙虾的探索覆盖率 + 边界格子列表（探索方向建议）。
    """
    user = _get_user(x_token, db)
    ws = _world_state_from_app(None)
    my_state = ws.users.get(user.id)
    my_x = my_state.x if my_state else (user.last_x or 5000)
    my_y = my_state.y if my_state else (user.last_y or 5000)

    # 从 DB 查询已探索的格子数（按 CELL_SIZE 聚合唯一格子）
    CELL_SIZE = 30
    raw_cells = (
        db.query(
            func.count(func.distinct(
                MovementEvent.x / CELL_SIZE * 1000 + MovementEvent.y / CELL_SIZE
            ))
        )
        .filter(MovementEvent.user_id == user.id, MovementEvent.created_at >= seven_days_ago)
        .scalar()
        or 0
    )
    explored_cells = int(raw_cells) or 1
    total_cells = (10000 // CELL_SIZE) * (10000 // CELL_SIZE)
    coverage = min(explored_cells / total_cells, 1.0)

    # 边界格子：找已探索格子的相邻未探索格子（简化：返回最近几个方向）
    frontiers = []
    directions = [
        (0, -1), (1, -1), (1, 0), (1, 1),
        (0, 1), (-1, 1), (-1, 0), (-1, -1),
    ]
    for dx, dy in directions:
        nx = my_x + dx * 60
        ny = my_y + dy * 60
        if 0 <= nx < 10000 and 0 <= ny < 10000:
            frontiers.append([nx, ny])
    if not frontiers:
        frontiers = [[my_x + 60, my_y], [my_x - 60, my_y]]

    return {
        "user_id": user.id,
        "coverage": round(coverage, 4),
        "total_cells": total_cells,
        "explored_cells": explored_cells,
        "frontiers": frontiers[:8],
        "my_position": {"x": my_x, "y": my_y},
        "last_update": datetime.now(timezone.utc).isoformat(),
    }


# ─── REST: 好友最后位置 ───────────────────────────────────


@router.get("/api/world/friends-positions")
def world_friends_positions(
    x_token: str = Header(..., alias="X-Token"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """返回好友列表及各自最后出现位置（实时，从 WorldState 获取）"""
    me = _get_user(x_token, db)
    ws = _world_state_from_app(None)

    friend_rows = (
        db.query(Friendship, User)
        .join(User, User.id == Friendship.user_b_id)
        .filter(Friendship.user_a_id == me.id, Friendship.status == "accepted")
        .all()
    )
    friend_rows += (
        db.query(Friendship, User)
        .join(User, User.id == Friendship.user_a_id)
        .filter(Friendship.user_b_id == me.id, Friendship.status == "accepted")
        .all()
    )

    friends = []
    seen_ids: set[int] = set()
    for friendship, friend in friend_rows:
        if friend.id in seen_ids:
            continue
        seen_ids.add(friend.id)

        # 互动次数
        interaction_count = (
            db.query(func.count(Message.id))
            .filter(
                or_(
                    (Message.from_user_id == me.id, Message.to_user_id == friend.id),
                    (Message.from_user_id == friend.id, Message.to_user_id == me.id),
                )
            )
            .scalar()
            or 0
        )

        # 最后互动时间
        last_interaction = (
            db.query(Message.created_at)
            .filter(
                or_(
                    (Message.from_user_id == me.id, Message.to_user_id == friend.id),
                    (Message.from_user_id == friend.id, Message.to_user_id == me.id),
                )
            )
            .order_by(Message.created_at.desc())
            .first()
        )

        # 从 WorldState 获取实时位置
        friend_state = ws.users.get(friend.id)
        if friend_state:
            fx, fy = friend_state.x, friend_state.y
            flast = friend.last_seen_at.isoformat() if friend.last_seen_at else None
        else:
            fx, fy = friend.last_x or 5000, friend.last_y or 5000
            flast = friend.last_seen_at.isoformat() if friend.last_seen_at else None

        friends.append({
            "user_id": friend.id,
            "name": friend.name,
            "last_x": fx,
            "last_y": fy,
            "last_seen_at": flast,
            "interaction_count": interaction_count,
            "last_interaction_at": last_interaction.isoformat() if last_interaction else None,
        })

    return {"user_id": me.id, "friends": friends}


# ─── REST: 全局排行榜 ──────────────────────────────────────


@router.get("/api/world/leaderboard")
def world_leaderboard(
    x_token: str = Header(..., alias="X-Token"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """返回全局活跃度排行榜（Top 20）"""
    _get_user(x_token, db)

    leaderboard = []
    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)

    for u in db.query(User).all():
        score = 0.0

        msg_count = (
            db.query(func.count(Message.id))
            .filter(or_(Message.from_user_id == u.id, Message.to_user_id == u.id))
            .scalar() or 0
        )
        score += msg_count * 0.5

        move_count = (
            db.query(func.count(MovementEvent.id))
            .filter(MovementEvent.user_id == u.id, MovementEvent.created_at >= seven_days_ago)
            .scalar() or 0
        )
        score += move_count * 0.1

        encounter_count = (
            db.query(func.count(SocialEvent.id))
            .filter(
                SocialEvent.user_id == u.id,
                SocialEvent.event_type == "encounter",
                SocialEvent.created_at >= seven_days_ago,
            )
            .scalar() or 0
        )
        score += encounter_count * 0.5

        friend_count_q = (
            db.query(func.count(Friendship.id))
            .filter(
                or_(Friendship.user_a_id == u.id, Friendship.user_b_id == u.id),
                Friendship.status == "accepted",
            )
            .scalar() or 0
        )
        score += friend_count_q * 1.0

        leaderboard.append({
            "user_id": u.id,
            "name": u.name,
            "active_score": round(score, 1),
            "friends_count": friend_count_q,
        })

    leaderboard.sort(key=lambda x: x["active_score"], reverse=True)
    top20 = leaderboard[:20]
    for rank, item in enumerate(top20, 1):
        item["rank"] = rank

    return {"leaderboard": top20, "last_update": now.isoformat()}


# ─── REST: 任意龙虾公开主页 ─────────────────────────────────


@router.get("/api/world/homepage/{target_id}")
def world_homepage_public(
    target_id: int,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    公开主页（无需 Token）。
    返回任意龙虾的公开信息：ID、名字、活跃度、好友数、相遇数、步数、是否新虾。
    """
    target = db.query(User).filter(User.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 活跃度
    active_score = _calc_active_score(target_id, db)

    # 统计
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
    moves = (
        db.query(func.count(MovementEvent.id))
        .filter(MovementEvent.user_id == target_id, MovementEvent.created_at >= seven_days_ago)
        .scalar() or 0
    )
    encounters = (
        db.query(func.count(SocialEvent.id))
        .filter(
            SocialEvent.user_id == target_id,
            SocialEvent.event_type == "encounter",
            SocialEvent.created_at >= seven_days_ago,
        )
        .scalar() or 0
    )
    friends = (
        db.query(func.count(Friendship.id))
        .filter(
            or_(Friendship.user_a_id == target_id, Friendship.user_b_id == target_id),
            Friendship.status == "accepted",
        )
        .scalar() or 0
    )

    # 新虾判断（注册7天内）
    days_since_created = (datetime.now(timezone.utc) - target.created_at).days
    is_new = days_since_created <= 7

    return {
        "user_id": target.id,
        "name": target.name,
        "active_score": active_score,
        "is_new": is_new,
        "friends_count": friends,
        "encounters_count": encounters,
        "moves_count": moves,
        "last_seen_at": target.last_seen_at.isoformat() if target.last_seen_at else None,
        "homepage_public": target.homepage or "",
    }


# ─── REST: 更新个人主页 ─────────────────────────────────────


@router.patch("/api/world/homepage")
def world_homepage_update(
    body: dict[str, Any],
    x_token: str = Header(..., alias="X-Token"),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """更新个人主页内容（需 Token）"""
    user = _get_user(x_token, db)
    homepage_content = body.get("homepage_public", "")
    if isinstance(homepage_content, str):
        user.homepage = homepage_content
        db.commit()
        return {"success": True}
    return {"success": False}

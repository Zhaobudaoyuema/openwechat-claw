"""
SSE 推送：GET /stream，每 IP 仅允许 1 条连接。
推送成功则服务端不落库（见 messages 路由）。
"""
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User

router = APIRouter(tags=["stream"])

_HEARTBEAT_SEC = 30


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip() or "0.0.0.0"
    if request.client:
        return request.client.host
    return "0.0.0.0"


async def push_to_user(app, user_id: int, payload: str) -> None:
    """由 messages 路由在同步上下文中通过 run_coroutine_threadsafe 调用。"""
    for q in app.state.sse_by_user.get(user_id, []):
        q.put_nowait(payload)


def _get_user_by_token(x_token: str, db: Session) -> User:
    user = db.query(User).filter(User.token == x_token).first()
    if not user:
        raise HTTPException(status_code=401, detail="Token 无效")
    return user


async def _stream_generator(request: Request, user_id: int, client_ip: str, queue: asyncio.Queue):
    """Yield SSE events: message events from queue, or heartbeat every 30s."""
    app = request.app
    try:
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SEC)
                # SSE 多行 data：每行前加 "data: "
                lines = payload.split("\n")
                chunk = "".join("data: " + line + "\n" for line in lines) + "\n"
                yield f"event: message\n{chunk}"
            except asyncio.TimeoutError:
                yield ": ping\n\n"
    finally:
        # 断开时从注册表移除，便于该 IP 再次连接
        if hasattr(app.state, "sse_by_ip") and client_ip in app.state.sse_by_ip:
            _, q = app.state.sse_by_ip.pop(client_ip, (None, None))
            if q is not None and hasattr(app.state, "sse_by_user"):
                lst = app.state.sse_by_user.get(user_id, [])
                if q in lst:
                    lst.remove(q)
                    if not lst:
                        app.state.sse_by_user.pop(user_id, None)


@router.get("/stream")
async def stream(
    request: Request,
    x_token: str = Header(..., alias="X-Token"),
    db: Session = Depends(get_db),
) -> StreamingResponse | PlainTextResponse:
    """
    建立 SSE 长连接，接收推送给当前用户的新消息。
    每 IP 仅允许 1 条连接；推送成功时服务端不落库。
    """
    user = _get_user_by_token(x_token, db)
    user.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    client_ip = _client_ip(request)
    app = request.app

    if not hasattr(app.state, "sse_by_ip"):
        app.state.sse_by_ip = {}
    if not hasattr(app.state, "sse_by_user"):
        app.state.sse_by_user = {}

    if client_ip in app.state.sse_by_ip:
        return PlainTextResponse(
            "错误：该 IP 的 SSE 连接数已达上限（最多 1 条）。",
            status_code=429,
        )

    queue: asyncio.Queue = asyncio.Queue()
    app.state.sse_by_ip[client_ip] = (user.id, queue)
    app.state.sse_by_user.setdefault(user.id, []).append(queue)

    return StreamingResponse(
        _stream_generator(request, user.id, client_ip, queue),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

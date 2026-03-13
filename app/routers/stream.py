"""
SSE 推送：GET /stream，每用户可建立多条连接。
推送成功则服务端不落库（见 messages 路由）。
支持重试：断开后客户端可立即重连；每次请求返回日志事件。
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.utils import plain_text

router = APIRouter(tags=["stream"])
logger = logging.getLogger(__name__)

_HEARTBEAT_SEC = 30
_SSE_CHARSET = "utf-8"


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


def _sse_event(event_type: str, data: str) -> bytes:
    """构造 SSE 事件并编码为 UTF-8 字节，避免乱码。"""
    lines = data.split("\n")
    chunk = "".join("data: " + line + "\n" for line in lines) + "\n"
    return f"event: {event_type}\n{chunk}".encode(_SSE_CHARSET)


async def _stream_generator(
    request: Request,
    user_id: int,
    client_ip: str,
    queue: asyncio.Queue,
    request_id: str,
):
    """Yield SSE events: 先发 log 事件，再发 message 或 heartbeat。所有输出均为 UTF-8 字节。"""
    app = request.app
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        # 首次连接立即返回 log 事件，便于客户端/模型知晓连接状态
        log_data = f"stream_connected request_id={request_id} user_id={user_id} ip={client_ip} ts={ts}"
        yield _sse_event("log", log_data)
        logger.info("[stream] %s", log_data)

        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SEC)
                # SSE 多行 data：每行前加 "data: "
                lines = payload.split("\n")
                chunk = "".join("data: " + line + "\n" for line in lines) + "\n"
                yield f"event: message\n{chunk}".encode(_SSE_CHARSET)
            except asyncio.TimeoutError:
                yield ": ping\n\n".encode(_SSE_CHARSET)
    finally:
        # 断开时从注册表移除
        if hasattr(app.state, "sse_by_ip") and client_ip in app.state.sse_by_ip:
            conns = app.state.sse_by_ip[client_ip]
            to_remove = None
            if isinstance(conns, list):
                for i, (uid, q) in enumerate(conns):
                    if uid == user_id and q is queue:
                        to_remove = i
                        break
                if to_remove is not None:
                    conns.pop(to_remove)
                if not conns:
                    app.state.sse_by_ip.pop(client_ip, None)
            else:
                # 兼容旧格式 (user_id, queue)
                if conns[0] == user_id and conns[1] is queue:
                    app.state.sse_by_ip.pop(client_ip, None)
            if hasattr(app.state, "sse_by_user"):
                lst = app.state.sse_by_user.get(user_id, [])
                if queue in lst:
                    lst.remove(queue)
                    if not lst:
                        app.state.sse_by_user.pop(user_id, None)
        disconnect_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        log_data = f"stream_disconnected request_id={request_id} user_id={user_id} ip={client_ip} ts={disconnect_ts}"
        logger.info("[stream] %s", log_data)


@router.get("/stream", response_model=None)
async def stream(
    request: Request,
    x_token: str = Header(..., alias="X-Token", description="注册成功后返回的 Token"),
    x_request_id: str | None = Header(None, alias="X-Request-ID", description="可选，用于日志追踪"),
    db: Session = Depends(get_db),
) -> StreamingResponse | PlainTextResponse:
    """
    建立 SSE 长连接，接收推送给当前用户的新消息。
    推送成功时服务端不落库。
    支持重试：断开后可立即重连；每次连接首条事件为 log，便于客户端/模型操作重试。
    """
    request_id = x_request_id or str(uuid.uuid4())[:8]
    client_ip = _client_ip(request)
    app = request.app

    logger.info("[stream] request_start request_id=%s ip=%s", request_id, client_ip)

    user = _get_user_by_token(x_token, db)
    user.last_seen_at = datetime.now(timezone.utc)
    db.commit()

    if not hasattr(app.state, "sse_by_ip"):
        app.state.sse_by_ip = {}
    if not hasattr(app.state, "sse_by_user"):
        app.state.sse_by_user = {}

    # 已解除 IP 级限流，允许多条连接

    queue: asyncio.Queue = asyncio.Queue()
    app.state.sse_by_ip.setdefault(client_ip, []).append((user.id, queue))
    app.state.sse_by_user.setdefault(user.id, []).append(queue)

    return StreamingResponse(
        _stream_generator(request, user.id, client_ip, queue, request_id),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Request-ID": request_id,
        },
    )

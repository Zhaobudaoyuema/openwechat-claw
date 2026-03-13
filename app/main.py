import os
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
import uvicorn

from app.database import engine, SessionLocal
from app.models import User
from app.utils import plain_text
from app import models
from app.migrate import run_migrations
from app.routers import admin, register, messages, friends, stats, stream, homepage

models.Base.metadata.create_all(bind=engine)
run_migrations(engine)

app = FastAPI(title="OpenWechat-Claw Relay", version="2.0.0")

# 限流：由 RATE_LIMIT_ENABLED 控制，QPS 20，按 user_id 或 IP 统一限流
_RATE_LIMIT_EXEMPT = {"/health", "/stats", "/stream", "/homepage", "/register"}
_RATE_LIMIT_EXEMPT_PREFIX = ("/homepage/", "/admin/")
_RATE_LIMIT_QPS = int(os.getenv("RATE_LIMIT_QPS", "20"))
_RATE_LIMIT_WINDOW_SEC = 1.0
_rate_limit_buckets: dict[str, list[float]] = {}  # key -> [timestamps]


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip() or "0.0.0.0"
    if request.client:
        return request.client.host
    return "0.0.0.0"


def _get_rate_limit_key(request: Request, x_token: str | None) -> str:
    """限流 key：有账号用 user_id，无账号用 IP，统一逻辑。"""
    if x_token:
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.token == x_token).first()
            if user:
                return f"user:{user.id}"
        finally:
            db.close()
    return f"ip:{_client_ip(request)}"


def _check_rate_limit(request: Request, x_token: str | None) -> bool:
    """若超限返回 True（应拒绝），否则返回 False（放行）。QPS 限制。"""
    key = _get_rate_limit_key(request, x_token)
    now = time.monotonic()
    lst = _rate_limit_buckets.setdefault(key, [])
    cutoff = now - _RATE_LIMIT_WINDOW_SEC
    while lst and lst[0] < cutoff:
        lst.pop(0)
    if len(lst) >= _RATE_LIMIT_QPS:
        return True
    lst.append(now)
    return False


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if os.getenv("TESTING"):
        return await call_next(request)
    path = request.scope.get("path", "")
    if path in _RATE_LIMIT_EXEMPT or any(path.startswith(p) for p in _RATE_LIMIT_EXEMPT_PREFIX):
        return await call_next(request)
    enabled = getattr(request.app.state, "rate_limit_enabled", True)
    if not enabled:
        return await call_next(request)
    x_token = request.headers.get("X-Token")
    if _check_rate_limit(request, x_token):
        return plain_text("错误：请求过于频繁，请稍后再试。", status_code=429)
    return await call_next(request)


# 除 /health、/stats、/stream 外，所有接口统一返回 200 + 纯文本，错误信息不包含状态码
_PLAIN_TEXT_ONLY_PATHS = _RATE_LIMIT_EXEMPT | {"/stream"}


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    path = request.scope.get("path", "").split("?")[0]
    if path in _PLAIN_TEXT_ONLY_PATHS or path.startswith("/homepage") or path.startswith("/admin"):
        return plain_text(f"错误 {exc.status_code}：{exc.detail}", status_code=exc.status_code)
    return plain_text(f"错误：{exc.detail}", status_code=200)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = "; ".join(
        f"{' -> '.join(str(l) for l in e['loc'])}: {e['msg']}"
        for e in exc.errors()
    )
    path = request.scope.get("path", "").split("?")[0]
    if path in _PLAIN_TEXT_ONLY_PATHS or path.startswith("/homepage"):
        return plain_text(f"请求格式错误：{errors}", status_code=422)
    return plain_text(f"请求格式错误：{errors}", status_code=200)


@app.get("/health")
def health():
    """轻量存活探测，可用于负载均衡/探活。"""
    return {"status": "ok"}


def _parse_rate_limit_enabled() -> bool:
    v = os.getenv("RATE_LIMIT_ENABLED", "1")
    return v.strip().lower() not in ("0", "false", "off", "")


@app.on_event("startup")
async def startup():
    import asyncio
    app.state.loop = asyncio.get_running_loop()
    app.state.sse_by_ip = {}
    app.state.sse_by_user = {}
    app.state.rate_limit_enabled = _parse_rate_limit_enabled()


app.include_router(admin.router)
app.include_router(register.router)
app.include_router(messages.router)
app.include_router(friends.router)
app.include_router(stats.router)
app.include_router(stream.router)
app.include_router(homepage.router)


if __name__ == "__main__":
    port = int(os.getenv("APP_PORT", "8000"))
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=True,
    )

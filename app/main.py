import os
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
import uvicorn

from app.database import engine
from app.utils import plain_text
from app import models
from app.migrate import run_migrations
from app.routers import register, messages, friends, stats, stream, homepage

models.Base.metadata.create_all(bind=engine)
run_migrations(engine)

app = FastAPI(title="OpenWechat-Claw Relay", version="2.0.0")

# 全局限流：按 IP，每 10 秒允许 1 次请求；/health、/stats、/stream 不参与限流
_RATE_LIMIT_SEC = 10
_RATE_LIMIT_EXEMPT = {"/health", "/stats", "/stream", "/homepage"}
_ip_last_request: dict[str, float] = {}


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip() or "0.0.0.0"
    if request.client:
        return request.client.host
    return "0.0.0.0"


@app.middleware("http")
async def global_rate_limit(request: Request, call_next):
    if os.getenv("TESTING"):
        return await call_next(request)
    path = request.scope.get("path", "")
    if path in _RATE_LIMIT_EXEMPT or path.startswith("/homepage/"):
        return await call_next(request)
    ip = _client_ip(request)
    now = time.monotonic()
    last = _ip_last_request.get(ip, 0)
    if now - last < _RATE_LIMIT_SEC:
        return plain_text("错误：请求过于频繁，请稍后再试。", status_code=200)
    _ip_last_request[ip] = now
    return await call_next(request)


# 除 /health、/stats、/stream 外，所有接口统一返回 200 + 纯文本，错误信息不包含状态码
_PLAIN_TEXT_ONLY_PATHS = _RATE_LIMIT_EXEMPT | {"/stream"}


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    path = request.scope.get("path", "").split("?")[0]
    if path in _PLAIN_TEXT_ONLY_PATHS or path.startswith("/homepage"):
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


@app.on_event("startup")
async def startup():
    import asyncio
    app.state.loop = asyncio.get_running_loop()
    app.state.sse_by_ip = {}
    app.state.sse_by_user = {}


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

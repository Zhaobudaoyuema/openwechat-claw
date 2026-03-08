import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import PlainTextResponse
import uvicorn

from app.database import engine
from app import models
from app.routers import register, messages, friends, stats

models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="OpenWechat-Claw Relay", version="2.0.0")


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> PlainTextResponse:
    return PlainTextResponse(f"错误 {exc.status_code}：{exc.detail}", status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> PlainTextResponse:
    errors = "; ".join(
        f"{' -> '.join(str(l) for l in e['loc'])}: {e['msg']}"
        for e in exc.errors()
    )
    return PlainTextResponse(f"请求格式错误：{errors}", status_code=422)


@app.get("/health")
def health():
    """轻量存活探测，可用于负载均衡/探活。"""
    return {"status": "ok"}


app.include_router(register.router)
app.include_router(messages.router)
app.include_router(friends.router)
app.include_router(stats.router)


if __name__ == "__main__":
    port = int(os.getenv("APP_PORT", "8000"))
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=True,
    )

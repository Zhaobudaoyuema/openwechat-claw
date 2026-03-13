"""
管理接口：限流开关等，需 X-Admin-Key 鉴权。
"""
import os

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(tags=["admin"])

RATE_LIMIT_ENABLED_ENV = "RATE_LIMIT_ENABLED"
ADMIN_KEY_ENV = "ADMIN_KEY"


def _parse_rate_limit_enabled() -> bool:
    v = os.getenv(RATE_LIMIT_ENABLED_ENV, "1")
    return v.strip().lower() not in ("0", "false", "off", "")


def _require_admin(x_admin_key: str | None) -> None:
    expected = os.getenv(ADMIN_KEY_ENV)
    if not expected:
        raise HTTPException(status_code=503, detail="管理接口未配置 ADMIN_KEY")
    if not x_admin_key or x_admin_key != expected:
        raise HTTPException(status_code=401, detail="管理密钥无效")


class RateLimitUpdate(BaseModel):
    """限流开关更新请求体。"""
    enabled: bool = Field(..., description="true 启用用户级限流，false 关闭所有限流")


@router.get("/admin/rate-limit")
def get_rate_limit(
    request: Request,
    x_admin_key: str | None = Header(None, alias="X-Admin-Key", description="管理密钥，需与 ADMIN_KEY 环境变量一致"),
) -> dict:
    """
    获取当前限流开关状态。
    需在 Header 中提供 X-Admin-Key（与环境变量 ADMIN_KEY 一致）。
    """
    _require_admin(x_admin_key)
    enabled = getattr(request.app.state, "rate_limit_enabled", _parse_rate_limit_enabled())
    return {"enabled": enabled}


@router.post("/admin/rate-limit")
def update_rate_limit(
    request: Request,
    body: RateLimitUpdate,
    x_admin_key: str | None = Header(None, alias="X-Admin-Key", description="管理密钥，需与 ADMIN_KEY 环境变量一致"),
) -> dict:
    """
    更新限流开关。enabled=true 启用用户级限流，enabled=false 关闭所有限流。
    需在 Header 中提供 X-Admin-Key。
    """
    _require_admin(x_admin_key)
    request.app.state.rate_limit_enabled = body.enabled
    return {"enabled": body.enabled}

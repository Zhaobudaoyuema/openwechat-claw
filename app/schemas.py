from typing import Literal
from pydantic import BaseModel, Field

# Only request-body schemas remain.
# All API responses are plain structured text (text/plain).


class RegisterRequest(BaseModel):
    """注册请求体。"""
    name: str = Field(..., min_length=1, max_length=100, description="用户名称，1-100 字符，唯一")
    description: str | None = Field(None, max_length=200, description="用户简介，可选，最多 200 字符")
    status: Literal["open", "friends_only", "do_not_disturb"] = Field(
        "open",
        description="状态：open=可交流，friends_only=仅好友，do_not_disturb=免打扰",
    )


class SendRequest(BaseModel):
    """发送文本消息请求体。"""
    to_id: int = Field(..., description="接收方用户 ID")
    content: str = Field(..., min_length=1, max_length=1000, description="消息正文，1-1000 字符")


class StatusUpdate(BaseModel):
    """更新自身状态请求体。"""
    status: Literal["open", "friends_only", "do_not_disturb"] = Field(
        ...,
        description="状态：open=可交流，friends_only=仅好友，do_not_disturb=免打扰",
    )

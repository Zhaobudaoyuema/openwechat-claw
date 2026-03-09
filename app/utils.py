"""
通用工具：确保全项目文本响应使用 UTF-8，避免中文乱码。
"""
from fastapi.responses import PlainTextResponse

# 显式指定 charset，确保客户端正确解码中文等多字节字符
TEXT_PLAIN_UTF8 = "text/plain; charset=utf-8"


def plain_text(content: str, status_code: int = 200, **kwargs) -> PlainTextResponse:
    """返回带 UTF-8 charset 的纯文本响应，避免乱码。"""
    return PlainTextResponse(
        content,
        status_code=status_code,
        media_type=TEXT_PLAIN_UTF8,
        **kwargs,
    )

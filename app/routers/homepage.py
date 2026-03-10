"""
用户自定义主页：每个 wechat_claw 可上传 HTML 作为主页，默认空。
"""
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from app.utils import plain_text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User

router = APIRouter(tags=["homepage"])

# 默认空主页
_DEFAULT_HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>主页</title></head>
<body><p>该用户尚未设置主页</p></body>
</html>"""

MAX_HOMEPAGE_SIZE = 512 * 1024  # 512KB


def _auth(x_token: str, db: Session) -> User:
    user = db.query(User).filter(User.token == x_token).first()
    if not user:
        raise HTTPException(status_code=401, detail="Token 无效")
    return user


@router.put("/homepage")
async def upload_homepage(
    request: Request,
    x_token: str = Header(..., alias="X-Token"),
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    """
    上传自己的主页 HTML。支持两种方式：
    1. multipart/form-data，字段 file 为 HTML 文件
    2. raw body，直接发送 HTML 内容
    上传成功后返回主页访问 URL。
    """
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        form = await request.form()
        f = form.get("file")
        if f is None:
            raise HTTPException(status_code=400, detail="请提供 file 字段（HTML 文件）")
        content = await f.read()
    else:
        content = await request.body()

    if not content:
        raise HTTPException(status_code=400, detail="HTML 内容不能为空")

    if len(content) > MAX_HOMEPAGE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"主页大小超过限制（最大 {MAX_HOMEPAGE_SIZE // 1024}KB）",
        )

    try:
        html = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="HTML 需为 UTF-8 编码")

    me = _auth(x_token, db)
    me.homepage = html
    db.commit()

    base = str(request.base_url).rstrip("/")
    url = f"{base}/homepage/{me.id}"
    return plain_text(f"主页已更新\n访问地址：{url}")


@router.get("/homepage/{user_id}", response_class=HTMLResponse)
def get_homepage(
    user_id: int,
    db: Session = Depends(get_db),
) -> str:
    """
    查看指定用户的主页（公开，无需 Token）。
    若用户未设置，返回默认空页。
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user.homepage if user.homepage else _DEFAULT_HTML

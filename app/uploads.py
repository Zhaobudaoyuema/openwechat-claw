"""
文件上传存储：将上传的文件保存到 uploads 目录，返回存储路径。
"""
import logging
import os
import re
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

logger = logging.getLogger(__name__)

# 默认存储目录，可通过 UPLOADS_DIR 环境变量覆盖；未设置时使用项目根目录下的绝对路径
_default_uploads = Path(__file__).resolve().parent.parent / "uploads"
UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", str(_default_uploads)))
MAX_FILE_SIZE = int(os.getenv("MAX_UPLOAD_SIZE_MB", "10")) * 1024 * 1024  # 默认 10MB
ALLOWED_EXTENSIONS = None  # None 表示不限制扩展名


def _sanitize_filename(name: str) -> str:
    """保留扩展名，替换不安全字符。"""
    if not name or name.strip() == ".":
        return "file"
    base = os.path.basename(name)
    # 移除路径遍历等危险字符
    safe = re.sub(r'[^\w\s\-\.]', "_", base, flags=re.IGNORECASE)
    return safe.strip() or "file"


async def save_upload(file: UploadFile) -> tuple[str, str]:
    """
    保存上传文件，返回 (attachment_path, attachment_filename)。
    attachment_path 用于存储和 URL 路径，attachment_filename 为原始文件名用于展示。
    """
    original = file.filename or "file"
    attachment_filename = _sanitize_filename(original)
    unique = str(uuid.uuid4())[:8]
    stored_name = f"{unique}_{attachment_filename}"
    attachment_path = str(UPLOADS_DIR / stored_name)

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"文件大小超过限制（最大 {MAX_FILE_SIZE // (1024*1024)}MB）",
        )

    file_path = UPLOADS_DIR / stored_name
    try:
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        with open(file_path, "wb") as f:
            f.write(content)
    except OSError as e:
        logger.exception("文件保存失败: path=%s", file_path)
        raise HTTPException(
            status_code=500,
            detail=f"文件保存失败：{str(e)}",
        ) from e

    # attachment_path 用于 DB 与 URL，仅存文件名；attachment_filename 为原始名用于展示
    return stored_name, original


def delete_upload(stored_name: str) -> None:
    """删除已中转的文件，发出即删。"""
    path = UPLOADS_DIR / stored_name
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass

from __future__ import annotations

import base64
import binascii
import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, Field

PDF_PREFIX = "resumes_pdf"
DEFAULT_STORAGE_ROOT = "/srv/wxresume"
DEFAULT_DOWNLOAD_SECRET = "wxresume-download-secret"
DEFAULT_DOWNLOAD_TTL = 300


def _load_positive_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _sanitize_openid(value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        raise ValueError("openid不能为空")
    if any(sep in trimmed for sep in ("/", "\\")):
        raise ValueError("openid非法")
    return trimmed


class InvalidResultId(ValueError):
    """Raised when the provided result identifier is invalid."""


class ResultNotFound(Exception):
    """Raised when a result PDF cannot be located."""


@dataclass
class StoredResult:
    openid: str
    filename: str
    path: Path
    size: int
    created_at: datetime


def encode_result_id(openid: str, filename: str) -> str:
    safe_openid = _sanitize_openid(openid)
    safe_filename = Path(filename).name
    raw = f"{safe_openid}/{safe_filename}"
    token = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")
    return token.rstrip("=")


def decode_result_id(result_id: str) -> Tuple[str, str]:
    padding = "=" * (-len(result_id) % 4)
    try:
        decoded = base64.urlsafe_b64decode((result_id + padding).encode("ascii")).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidResultId("无法解析结果ID") from exc
    if "/" not in decoded:
        raise InvalidResultId("结果ID缺少分隔符")
    openid, filename = decoded.split("/", 1)
    try:
        openid = _sanitize_openid(openid)
    except ValueError as exc:
        raise InvalidResultId(str(exc)) from exc
    parts = Path(filename).parts
    if len(parts) != 1 or parts[0] in ("", ".", ".."):
        raise InvalidResultId("文件名非法")
    name = parts[0]
    if not name.lower().endswith(".pdf"):
        raise InvalidResultId("仅支持PDF文件")
    return openid, name


class ResultStorage:
    def __init__(self, root: Path, prefix: str = PDF_PREFIX):
        self.root = Path(root)
        self.pdf_root = self.root / prefix
        self.queue_root = self.root / "reopt_queue"

    def list_results(self, openid: str, limit: int) -> List[StoredResult]:
        safe_openid = _sanitize_openid(openid)
        base_dir = self.pdf_root / safe_openid
        if not base_dir.exists():
            return []
        items: List[StoredResult] = []
        for entry in base_dir.glob("*.pdf"):
            if not entry.is_file():
                continue
            try:
                path = entry.resolve()
                stat = path.stat()
            except FileNotFoundError:
                continue
            created_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            items.append(
                StoredResult(
                    openid=safe_openid,
                    filename=path.name,
                    path=path,
                    size=stat.st_size,
                    created_at=created_at,
                )
            )
        items.sort(key=lambda item: item.created_at, reverse=True)
        return items[:limit]

    def get_result(self, openid: str, filename: str) -> StoredResult:
        safe_openid = _sanitize_openid(openid)
        base_dir = (self.pdf_root / safe_openid).resolve()
        if not base_dir.exists():
            raise ResultNotFound("结果不存在")
        target = (base_dir / filename).resolve()
        try:
            target.relative_to(base_dir)
        except ValueError as exc:
            raise ResultNotFound("非法文件路径") from exc
        if not target.is_file():
            raise ResultNotFound("结果不存在")
        if target.suffix.lower() != ".pdf":
            raise ResultNotFound("结果类型错误")
        stat = target.stat()
        created_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        return StoredResult(
            openid=safe_openid,
            filename=target.name,
            path=target,
            size=stat.st_size,
            created_at=created_at,
        )

    def enqueue_reopt(self, result_id: str, record: StoredResult) -> str:
        self.queue_root.mkdir(parents=True, exist_ok=True)
        event_id = f"{int(time.time() * 1000)}_{uuid.uuid4().hex}"
        payload = {
            "event_id": event_id,
            "result_id": result_id,
            "openid": record.openid,
            "filename": record.filename,
            "source_pdf": str(record.path),
            "created_at": record.created_at.isoformat(),
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }
        event_path = self.queue_root / f"{event_id}.json"
        event_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return event_id


class TokenError(Exception):
    """Base class for download token errors."""


class TokenExpired(TokenError):
    """Raised when a download token has expired."""


class InvalidToken(TokenError):
    """Raised when a download token is invalid."""


class DownloadSigner:
    def __init__(self, secret_key: str, expires_in: int):
        self.serializer = URLSafeTimedSerializer(secret_key, salt="wxresume-results")
        self.expires_in = expires_in

    def issue(self, result_id: str) -> str:
        return self.serializer.dumps({"rid": result_id})

    def validate(self, token: str) -> str:
        try:
            payload = self.serializer.loads(token, max_age=self.expires_in)
        except SignatureExpired as exc:
            raise TokenExpired("下载token已过期") from exc
        except BadSignature as exc:
            raise InvalidToken("下载token无效") from exc
        rid = payload.get("rid")
        if not isinstance(rid, str):
            raise InvalidToken("下载token内容错误")
        return rid


router = APIRouter(prefix="/results", tags=["results"])

_storage_root = Path(os.getenv("WXRESUME_STORAGE_ROOT", DEFAULT_STORAGE_ROOT))
_download_secret = os.getenv("WXRESUME_DOWNLOAD_SECRET", DEFAULT_DOWNLOAD_SECRET)
_download_ttl = _load_positive_int(os.getenv("WXRESUME_DOWNLOAD_TTL"), DEFAULT_DOWNLOAD_TTL)

storage = ResultStorage(_storage_root)
download_signer = DownloadSigner(_download_secret, _download_ttl)


class ResultListEntry(BaseModel):
    id: str = Field(..., description="结果ID（URL安全Base64）")
    openid: str = Field(..., description="用户openid")
    filename: str = Field(..., description="PDF文件名")
    size: int = Field(..., description="文件大小（字节）")
    created_at: datetime = Field(..., description="生成时间（UTC ISO8601）")
    view_url: str = Field(..., description="在线预览链接")
    download_url: str = Field(..., description="下载接口地址")


class ResultListResponse(BaseModel):
    ok: bool = True
    results: List[ResultListEntry] = Field(default_factory=list)


class DownloadTicket(BaseModel):
    ok: bool = True
    url: str = Field(..., description="带签名的下载链接")
    token: str = Field(..., description="下载token")
    expires_in: int = Field(..., description="有效期（秒）")


class ReoptResponse(BaseModel):
    ok: bool = True
    event_id: str = Field(..., description="再次优化任务ID")


@router.get("", response_model=ResultListResponse, summary="列出最近10个PDF")
def list_results(request: Request, openid: str = Query(..., min_length=1, description="用户openid")) -> ResultListResponse:
    records = storage.list_results(openid=openid, limit=10)
    entries: List[ResultListEntry] = []
    for record in records:
        result_id = encode_result_id(record.openid, record.filename)
        view_url = str(request.url_for("view_result", result_id=result_id))
        download_url = str(request.url_for("download_result", result_id=result_id))
        entries.append(
            ResultListEntry(
                id=result_id,
                openid=record.openid,
                filename=record.filename,
                size=record.size,
                created_at=record.created_at,
                view_url=view_url,
                download_url=download_url,
            )
        )
    return ResultListResponse(ok=True, results=entries)


@router.get("/{result_id}/view", name="view_result", summary="预览PDF")
def view_result(result_id: str) -> FileResponse:
    try:
        openid, filename = decode_result_id(result_id)
        record = storage.get_result(openid, filename)
    except InvalidResultId as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ResultNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="结果不存在") from exc
    headers = {"Content-Disposition": f'inline; filename="{record.filename}"'}
    return FileResponse(record.path, media_type="application/pdf", headers=headers)


@router.get("/{result_id}/download", name="download_result", summary="下载PDF或获取临时签名")
def download_result(
    result_id: str,
    request: Request,
    token: str | None = Query(default=None, description="下载token，可选"),
):
    try:
        openid, filename = decode_result_id(result_id)
        record = storage.get_result(openid, filename)
    except InvalidResultId as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ResultNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="结果不存在") from exc

    if token is None:
        token = download_signer.issue(result_id)
        url = str(request.url.include_query_params(token=token))
        return DownloadTicket(ok=True, url=url, token=token, expires_in=download_signer.expires_in)

    try:
        verified = download_signer.validate(token)
    except TokenExpired as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="下载token已过期") from exc
    except InvalidToken as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="下载token无效") from exc

    if verified != result_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="下载token不匹配")

    return FileResponse(record.path, media_type="application/pdf", filename=record.filename)


@router.post("/{result_id}/reopt", response_model=ReoptResponse, summary="触发再次优化")
def reopt_result(result_id: str) -> ReoptResponse:
    try:
        openid, filename = decode_result_id(result_id)
        record = storage.get_result(openid, filename)
    except InvalidResultId as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ResultNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="结果不存在") from exc

    event_id = storage.enqueue_reopt(result_id, record)
    return ReoptResponse(ok=True, event_id=event_id)


__all__ = ["router"]

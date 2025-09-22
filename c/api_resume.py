# /srv/wxresume/c/api_resume.py
import os, time, base64, json, shutil
from typing import Any, Dict
import requests
from fastapi import APIRouter, Request, HTTPException

router = APIRouter(prefix="/api/resume", tags=["resume"])

RENDER_URL = "http://127.0.0.1:3000/render"   # Node 渲染服务
JSON_DIR   = "/srv/wxresume/resumes_json"
PDF_DIR    = "/srv/wxresume/resumes_pdf"
DEFAULT_THEME = "jsonresume-theme-flat"

def _ensure_dir(p: str):
    os.makedirs(p, exist_ok=True)

def _rid(openid: str, filename: str) -> str:
    return base64.urlsafe_b64encode(f"{openid}/{filename}".encode("utf-8")).decode("utf-8")

@router.post("/save")
async def save_resume(request: Request, openid: str, theme: str = DEFAULT_THEME) -> Dict[str, Any]:
    """
    Body 可直接传 JSON Resume（根对象），也可传 { "resume": {...}, "theme": "xxx" }
    openid 通过 query ?openid=xxx 传入
    """
    body = await request.json()
    resume = body.get("resume") if isinstance(body, dict) and "resume" in body else body
    if not isinstance(resume, dict) or not resume:
        raise HTTPException(status_code=400, detail="invalid resume json")

    # 1) 保存 JSON
    json_user_dir = os.path.join(JSON_DIR, openid)
    _ensure_dir(json_user_dir)
    latest_json = os.path.join(json_user_dir, "latest.json")
    with open(latest_json, "w", encoding="utf-8") as f:
        json.dump(resume, f, ensure_ascii=False, indent=2)

    # 2) 调用渲染服务生成 PDF
    try:
        r = requests.post(RENDER_URL, json={"resume": resume, "theme": theme}, timeout=60)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"render error: {e}")

    if not data.get("ok"):
        raise HTTPException(status_code=500, detail=f"render failed: {data}")

    tmp_pdf = data.get("pdf_path")
    if not tmp_pdf or not os.path.exists(tmp_pdf):
        raise HTTPException(status_code=500, detail="pdf not found from renderer")

    # 3) 落盘到用户目录
    ts = int(time.time())
    filename = f"{ts}.pdf"
    pdf_user_dir = os.path.join(PDF_DIR, openid)
    _ensure_dir(pdf_user_dir)
    dst_pdf = os.path.join(pdf_user_dir, filename)
    shutil.move(tmp_pdf, dst_pdf)

    # 4) 生成 rid 与结果链接（走已存在的 /results 路由）
    rid = _rid(openid, filename)
    view_url = f"/results/{rid}/view"
    download_url = f"/results/{rid}/download"

    return {"ok": True, "rid": rid, "view_url": view_url, "download_url": download_url}


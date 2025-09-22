# -*- coding: utf-8 -*-
# 函数C：提供H5下载页、文件下载接口；供微信菜单(type=view)直接打开
import os, glob
from datetime import datetime
from urllib.parse import quote
from flask import Flask, request, jsonify, send_file, Response

OUTPUT_DIR = os.getenv("OUTPUT_DIR","/srv/wxresume/output").rstrip("/")

app = Flask(__name__)

def _latest(pattern: str) -> str:
    files = glob.glob(pattern)
    if not files:
        return ""
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files[0]

def _latest_pair(openid: str):
    base = f"{OUTPUT_DIR}/{openid}"
    r = _latest(f"{base}/resume_*.pdf")
    a = _latest(f"{base}/analysis_*.pdf")
    return r, a

@app.get("/ping")
def ping():
    return jsonify({"ok": True, "service": "resume-c", "time": datetime.utcnow().isoformat()+"Z"})

# H5 下载页（微信自定义菜单可直接指向这个URL）
@app.get("/h5/download")
def h5_download():
    openid = (request.args.get("openid") or "").strip()
    if not openid:
        return Response("<h3>缺少 openid</h3>", mimetype="text/html")
    r, a = _latest_pair(openid)
    html = [
        "<!doctype html><meta charset='utf-8'><title>下载我的简历</title>",
        "<style>body{font-family:system-ui,-apple-system,Segoe UI,Roboto,PingFang SC,Microsoft YaHei,Arial;margin:26px;}"
        ".btn{display:inline-block;margin:10px 0;padding:12px 18px;border-radius:10px;background:#2aa198;color:#fff;text-decoration:none}"
        ".hint{color:#666;margin-top:10px}</style>",
        "<h2>简历与分析下载</h2>",
        f"<p class='hint'>用户：{openid}</p>"
    ]
    if r:
        html.append(f"<p><a class='btn' href='/file?path={quote(r)}' target='_blank'>下载简历 PDF</a></p>")
    else:
        html.append("<p class='hint'>未找到简历，请先生成。</p>")
    if a:
        html.append(f"<p><a class='btn' href='/file?path={quote(a)}' target='_blank'>下载分析 PDF</a></p>")
    else:
        html.append("<p class='hint'>未找到分析报告。</p>")
    return Response("".join(html), mimetype="text/html;charset=utf-8")

# 直下发文件
@app.get("/file")
def file_get():
    path = request.args.get("path") or ""
    # 安全限制：必须在 OUTPUT_DIR 内
    path = os.path.abspath(path)
    if not path.startswith(os.path.abspath(OUTPUT_DIR)) or not os.path.exists(path):
        return Response("文件不存在", status=404)
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))

# JSON API（可选）
@app.get("/latest_api")
def latest_api():
    openid = (request.args.get("openid") or "").strip()
    if not openid:
        return jsonify({"ok":False,"error":"missing openid"}), 400
    r, a = _latest_pair(openid)
    return jsonify({"ok":True, "resume": r or None, "analysis": a or None})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8001)

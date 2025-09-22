# app.py
import os
import json
import time
import secrets
import urllib.request
from urllib.parse import quote, urlencode
from flask import Flask, request, redirect, Response

app = Flask(__name__)

def _html(msg: str):
    return Response(
        f"<!doctype html><meta charset='utf-8'><p>{msg}</p>",
        mimetype="text/html; charset=utf-8"
    )

# =========================
# 首页：发起微信 OAuth
# =========================
@app.route("/")
def index():
    appid = os.environ.get("APP_ID")
    if not appid:
        return _html("服务未配置 APP_ID")

    state = "resume." + secrets.token_urlsafe(12)
    redirect_uri = quote("https://" + request.host + "/wx/oauth/callback", safe="")

    auth_url = (
        "https://open.weixin.qq.com/connect/oauth2/authorize"
        f"?appid={appid}&redirect_uri={redirect_uri}"
        "&response_type=code&scope=snsapi_base"
        f"&state={state}#wechat_redirect"
    )
    return redirect(auth_url, code=302)

# 微信域名校验
@app.route("/MP_verify_jyKCpKPr5is1gYar.txt")
def wx_mp_verify():
    return Response("jyKCpKPr5is1gYar", mimetype="text/plain; charset=utf-8")

# =========================
# 回调：拿 openid/state → 跳问卷（预填隐藏）
# =========================
@app.route("/wx/oauth/callback")
def wx_callback():
    appid  = os.environ.get("APP_ID")
    secret = os.environ.get("APP_SECRET")
    form   = os.environ.get("FEISHU_FORM_URL")  # 问卷分享链接

    if not form:
        return _html("服务未配置 FEISHU_FORM_URL")

    code  = request.args.get("code")
    state = request.args.get("state") or ""
    if not code:
        return _html("授权失败：未获取到 code")

    # 用公众号凭证换取 openid
    url = (
        "https://api.weixin.qq.com/sns/oauth2/access_token"
        f"?appid={appid}&secret={secret}&code={code}&grant_type=authorization_code"
    )
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return _html(f"服务器异常：{e}")

    if data.get("errcode"):
        return _html(f"微信接口错误：{data.get('errcode')} {data.get('errmsg')}")

    openid = data.get("openid")
    if not openid:
        return _html("微信接口异常：未返回 openid")

    # 拼问卷 URL，预填并隐藏 openid/state/ts
    params = {
        "prefill_openid": openid, "hide_openid": "1",
        "prefill_state":  state,  "hide_state":  "1",
        "prefill_ts":     str(int(time.time())), "hide_ts": "1",
    }
    sep = '&' if '?' in form else '?'
    return redirect(f"{form}{sep}{urlencode(params)}", code=302)

# 健康检查
@app.route("/ping")
def ping():
    return "ok"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9000)

import os, json, urllib.parse, urllib.request
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse, PlainTextResponse

router = APIRouter(prefix="/wx", tags=["wechat"])

def _base_url(request: Request) -> str:
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", request.url.hostname)
    return f"{scheme}://{host}"

@router.get("/entry")
def entry(request: Request, state: str = "results"):
    appid = os.getenv("WX_APPID")
    if not appid:
        return PlainTextResponse("WX_APPID 未配置", status_code=500)
    redirect_uri = urllib.parse.quote_plus(_base_url(request) + "/wx/callback")
    url = (
        "https://open.weixin.qq.com/connect/oauth2/authorize"
        f"?appid={appid}&redirect_uri={redirect_uri}"
        "&response_type=code&scope=snsapi_base"
        f"&state={state}#wechat_redirect"
    )
    return RedirectResponse(url, status_code=302)

@router.get("/callback")
def callback(request: Request, code: str, state: str = "results"):
    appid = os.getenv("WX_APPID"); secret = os.getenv("WX_SECRET")
    if not appid or not secret:
        raise HTTPException(500, "WX_APPID/WX_SECRET 未配置")
    api = (
        "https://api.weixin.qq.com/sns/oauth2/access_token"
        f"?appid={appid}&secret={secret}&code={code}&grant_type=authorization_code"
    )
    with urllib.request.urlopen(api, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    openid = data.get("openid")
    if not openid:
        raise HTTPException(400, f"微信返回错误: {data}")
    dest = "/results?openid=" + urllib.parse.quote(openid)
    return RedirectResponse(_base_url(request) + dest, status_code=302)

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from importlib import import_module

app = FastAPI()

# 挂载静态目录
app.mount("/web", StaticFiles(directory="/srv/wxresume/web", html=True), name="web")

# 加载子模块
for modname in ("c.result", "c.wx_oauth", "c.api_resume"):
    try:
        mod = import_module(modname)
        router = getattr(mod, "router", None)
        if router is not None:
            app.include_router(router)
    except Exception:
        pass


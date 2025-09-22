from fastapi import FastAPI

from .result import router

app = FastAPI(title="WXResume Result Center", version="1.0.0")
app.include_router(router)

__all__ = ("app",)

"""账号池与登录采集接口，挂到现有 FastAPI 应用上。

用法（api.py 里加两行）：
    import account_api
    app.include_router(account_api.router)

提供：
    GET    /accounts                     账号列表
    POST   /accounts                     新增账号
    DELETE /accounts/{aid}               删除账号
    POST   /accounts/{aid}/login/start   启动无头登录（服务器端）
    GET    /accounts/{aid}/login/status  登录会话状态
    POST   /accounts/{aid}/login/sms     提交短信码
    POST   /accounts/{aid}/check         检测 Cookie 是否有效
    GET    /proxies                      代理列表
    POST   /proxies                      新增代理
    POST   /crawl/start                  启动异步采集
    GET    /crawl/status                 采集进度
"""

from __future__ import annotations

import asyncio
import threading
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import account_manager
import async_crawler
import login_session

router = APIRouter(tags=["accounts"])

STATE = {"running": False, "started": 0, "stats": None, "args": None}


class AccountIn(BaseModel):
    phone: str
    password: str
    note: str = ""


class SmsIn(BaseModel):
    code: str


class ProxyIn(BaseModel):
    url: str


class CrawlIn(BaseModel):
    cities: str = "bj"
    pages: str = "1"
    workers: int = 2
    headless: bool = True
    out: str = "data/crawl_async.jsonl"


@router.get("/accounts")
def list_accounts():
    return {"accounts": account_manager.list_accounts()}


@router.post("/accounts")
def add_account(a: AccountIn):
    try:
        aid = account_manager.add_account(a.phone, a.password, a.note)
    except Exception as exc:
        raise HTTPException(400, f"添加失败：{exc}")
    return {"id": aid}


@router.delete("/accounts/{aid}")
def del_account(aid: int):
    account_manager.delete_account(aid)
    return {"ok": True}


@router.post("/accounts/{aid}/login/start")
def login_start(aid: int, headless: bool = True):
    return login_session.start(aid, headless=headless)


@router.get("/accounts/{aid}/login/status")
def login_status(aid: int):
    return login_session.status(aid)


@router.post("/accounts/{aid}/login/sms")
def login_sms(aid: int, s: SmsIn):
    return login_session.submit_sms(aid, s.code)


@router.post("/accounts/{aid}/check")
def check_account(aid: int):
    return account_manager.check(aid)


@router.get("/proxies")
def list_proxies():
    return {"proxies": account_manager.list_proxies()}


@router.post("/proxies")
def add_proxy(p: ProxyIn):
    return {"id": account_manager.add_proxy(p.url)}


@router.post("/crawl/start")
def crawl_start(c: CrawlIn):
    if STATE["running"]:
        raise HTTPException(409, "已有采集任务在跑")
    STATE.update(running=True, started=int(time.time()), stats=None, args=c.dict())

    def _run():
        class Args:
            pass

        args = Args()
        for k, v in c.dict().items():
            setattr(args, k, v)
        try:
            stats = asyncio.run(async_crawler.run(args))
            STATE["stats"] = {"pages": stats.pages, "rows": stats.rows,
                              "captcha": stats.captcha, "captcha_ok": stats.captcha_ok,
                              "login_wall": stats.login_wall, "failed": stats.failed}
        except Exception as exc:
            STATE["stats"] = {"error": str(exc)[:200]}
        finally:
            STATE["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True}


@router.get("/crawl/status")
def crawl_status():
    return {"running": STATE["running"],
            "seconds": int(time.time() - STATE["started"]) if STATE["started"] else 0,
            "stats": STATE["stats"], "args": STATE["args"]}

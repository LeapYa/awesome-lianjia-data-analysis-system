"""链家账号池：账号入库、登录并缓存 Cookie、有效性检测。

存储用 SQLite（data/accounts.db），字段和项目里 Postgres 版一致，
迁移时把 SQL 换成 psycopg2 执行即可。

登录流程（clogin.lianjia.com/login）：
    手机号 + 密码 + 勾选协议 → 点登录 → 等待跳回 service 地址
    若出现短信/滑块验证，标记 needs_manual，等人工登录一次后再缓存

Cookie 缓存两点：
    1. storage_state（playwright 的 cookies + localStorage）落库，爬虫直接灌回去
    2. 单独存一份 cookies 列表，方便用 requests 复用
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

DB = Path("data/accounts.db")
DB.parent.mkdir(parents=True, exist_ok=True)

LOGIN_URL = "https://clogin.lianjia.com/login?service=https%3A%2F%2Fwww.lianjia.com%2Fuser%2Fcenter%2F"
HOME = "https://www.lianjia.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init() -> None:
    with conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            storage_state TEXT,
            cookies TEXT,
            status TEXT DEFAULT 'unknown',      -- unknown / ok / expired / needs_manual / error
            note TEXT DEFAULT '',
            last_login INTEGER DEFAULT 0,
            last_check INTEGER DEFAULT 0
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS proxies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE NOT NULL,
            enabled INTEGER DEFAULT 1,
            fails INTEGER DEFAULT 0,
            last_ok INTEGER DEFAULT 0
        )""")


# ------------------------------------------------------------------ 账号读写
def add_account(phone: str, password: str, note: str = "") -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO accounts(phone, password, note) VALUES (?,?,?)",
            (phone, password, note))
        return int(cur.lastrowid)


def list_accounts() -> list[dict]:
    with conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT id, phone, status, note, last_login, last_check, "
            "CASE WHEN storage_state IS NULL THEN 0 ELSE 1 END AS has_cookie "
            "FROM accounts ORDER BY id")]
    return rows


def get_account(account_id: int) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    return dict(r) if r else None


def pick_ready(n: int = 1) -> list[dict]:
    """取可用的账号（状态 ok 且有 cookie）。"""
    with conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM accounts WHERE status='ok' AND storage_state IS NOT NULL "
            "ORDER BY last_check DESC LIMIT ?", (n,))]
    return rows


def delete_account(account_id: int) -> None:
    with conn() as c:
        c.execute("DELETE FROM accounts WHERE id=?", (account_id,))


def set_status(account_id: int, status: str, note: str = "") -> None:
    with conn() as c:
        c.execute("UPDATE accounts SET status=?, note=?, last_check=? WHERE id=?",
                  (status, note, int(time.time()), account_id))


def save_state(account_id: int, storage_state: dict) -> None:
    cookies = storage_state.get("cookies") or []
    with conn() as c:
        c.execute("UPDATE accounts SET storage_state=?, cookies=?, status='ok', "
                  "last_login=? WHERE id=?",
                  (json.dumps(storage_state, ensure_ascii=False),
                   json.dumps(cookies, ensure_ascii=False),
                   int(time.time()), account_id))


def get_state(account_id: int) -> dict | None:
    a = get_account(account_id)
    if not a or not a.get("storage_state"):
        return None
    return json.loads(a["storage_state"])


# ------------------------------------------------------------------ 代理读写
def add_proxy(url: str) -> int:
    with conn() as c:
        cur = c.execute("INSERT OR IGNORE INTO proxies(url) VALUES (?)", (url,))
        return int(cur.lastrowid or 0)


def list_proxies(enabled_only: bool = False) -> list[dict]:
    q = "SELECT * FROM proxies" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY id"
    with conn() as c:
        return [dict(r) for r in c.execute(q)]


def proxy_failed(proxy_id: int) -> None:
    with conn() as c:
        c.execute("UPDATE proxies SET fails=fails+1, enabled=CASE WHEN fails>=3 THEN 0 ELSE enabled END "
                  "WHERE id=?", (proxy_id,))


def proxy_ok(proxy_id: int) -> None:
    with conn() as c:
        c.execute("UPDATE proxies SET fails=0, last_ok=? WHERE id=?",
                  (int(time.time()), proxy_id))


# ------------------------------------------------------------------ 登录
def login(account_id: int, headless: bool = False, timeout_s: float = 90.0) -> dict:
    """用账号密码登录并缓存 Cookie。返回 {ok, status, message}。"""
    from playwright.sync_api import sync_playwright

    a = get_account(account_id)
    if not a:
        return {"ok": False, "status": "missing", "message": "账号不存在"}

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=headless,
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)

        # 手机号 + 密码
        page.locator("input[placeholder*='手机号']").first.fill(a["phone"])
        page.locator("input[placeholder*='密码']").first.fill(a["password"])

        # 勾选协议（有的版本默认不勾）
        for sel in ("input[type=checkbox]", ".agree", "label:has-text('我已阅读')"):
            box = page.locator(sel)
            if box.count():
                try:
                    box.first.check(timeout=2000)
                except Exception:
                    try:
                        box.first.click(timeout=2000)
                    except Exception:
                        pass
                break

        page.locator("button:has-text('登录'), .btn-login, a:has-text('登录')").first.click()

        # 等跳转或出现验证
        t0 = time.time()
        status, msg = "error", ""
        while time.time() - t0 < timeout_s:
            page.wait_for_timeout(1500)
            url = page.url
            body = ""
            try:
                body = page.inner_text("body")[:400]
            except Exception:
                pass
            if "clogin" not in url and "lianjia.com" in url:
                status, msg = "ok", f"已登录，当前 {url[:60]}"
                break
            if any(k in body for k in ("短信", "动态码", "滑块", "拖动")):
                status, msg = "needs_manual", "需要短信或滑块验证，请在浏览器里手动完成一次"
                break
            if "密码错误" in body or "账号或密码" in body:
                status, msg = "error", "账号或密码不对"
                break

        if status == "ok":
            save_state(account_id, ctx.storage_state())
        else:
            set_status(account_id, status, msg)
        browser.close()
    return {"ok": status == "ok", "status": status, "message": msg}


def check(account_id: int) -> dict:
    """用缓存的 Cookie 访问第 2 页，判断登录态是否还有效。"""
    from playwright.sync_api import sync_playwright

    state = get_state(account_id)
    if not state:
        return {"ok": False, "status": "no_cookie", "message": "还没有 Cookie，先登录"}

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True,
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(user_agent=UA, storage_state=state)
        page = ctx.new_page()
        page.goto("https://bj.lianjia.com/ershoufang/pg2/",
                  wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)
        url = page.url
        n = page.locator("ul.sellListContent > li").count()
        browser.close()

    if n > 0:
        set_status(account_id, "ok", f"可用，第 2 页 {n} 条")
        return {"ok": True, "status": "ok", "message": f"第 2 页拿到 {n} 条"}
    if "clogin" in url:
        set_status(account_id, "expired", "Cookie 失效")
        return {"ok": False, "status": "expired", "message": "Cookie 失效，需要重新登录"}
    set_status(account_id, "unknown", f"没拿到列表，停在 {url[:50]}")
    return {"ok": False, "status": "unknown", "message": f"没拿到列表：{url[:60]}"}


init()

def manual_login(account_id: int, timeout_s: float = 600.0) -> dict:
    """打开有头浏览器，人工完成登录（密码、滑块、短信都人来做），
    离开登录页就保存 Cookie。滑块没把握时用这条路。"""
    from playwright.sync_api import sync_playwright

    a = get_account(account_id)
    if not a:
        return {"ok": False, "status": "missing", "message": "账号不存在"}

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=False,
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45000)

        t0 = time.time()
        while time.time() - t0 < timeout_s:
            page.wait_for_timeout(2000)
            if "clogin" not in page.url:
                save_state(account_id, ctx.storage_state())
                browser.close()
                return {"ok": True, "status": "ok", "message": f"已保存 Cookie：{page.url[:60]}"}
        browser.close()
    return {"ok": False, "status": "timeout", "message": "等待人工登录超时"}

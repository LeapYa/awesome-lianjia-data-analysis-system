"""链家登录会话：把"手机号密码 + 短信码 + 两道行为验证"拆成可人工介入的状态机。

为什么不能一把梭
----------------
登录一共四关：
    1. 手机号 + 密码
    2. 点「获取短信验证码」时弹滑块（行为验证）
    3. 输入 6 位短信码
    4. 提交登录时再弹一次极验 v4 点选

第 3 关机器拿不到（短信在用户手机上），第 2、4 关是行为验证，我们的 solver 能过。
于是设计成：机器负责 1、2、4，人只负责把短信码填进来。

状态机
------
    idle → starting → waiting_sms → verifying → ok
                        ↑              ↓
                        └── failed ←──┘

用法（由 admin_server 调用）：
    sess = LoginSession(account_id)
    sess.start()                 # 线程里跑到等短信或直接成功
    sess.status()                # {"state": "...", "message": "...", "need_sms": True}
    sess.submit_sms("123456")    # 人工填码
    sess.wait(120)               # 等结果
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import account_manager

LOGIN_URL = ("https://clogin.lianjia.com/login"
             "?service=https%3A%2F%2Fwww.lianjia.com%2Fuser%2Fcenter%2F")


class LoginSession:
    def __init__(self, account_id: int, headless: bool = False):
        self.account_id = account_id
        self.headless = headless
        self.state = "idle"
        self.message = ""
        self.need_sms = False
        self._sms = None
        self._sms_event = threading.Event()
        self._done = threading.Event()
        self._thread: threading.Thread | None = None
        self._stop = False

    # ---------------------------------------------------------- 对外接口
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.state = "starting"
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def status(self) -> dict:
        return {"state": self.state, "message": self.message,
                "need_sms": self.need_sms, "done": self._done.is_set()}

    def submit_sms(self, code: str) -> bool:
        if not self.need_sms:
            return False
        self._sms = code.strip()
        self.need_sms = False
        self.state = "verifying"
        self._sms_event.set()
        return True

    def cancel(self) -> None:
        self._stop = True
        self._sms_event.set()

    def wait(self, timeout_s: float = 180) -> bool:
        return self._done.wait(timeout_s)

    # ---------------------------------------------------------- 内部流程
    def _set(self, state: str, msg: str = "") -> None:
        self.state = state
        self.message = msg
        print(f"[登录 {self.account_id}] {state} {msg}", flush=True)

    def _run(self) -> None:
        from playwright.sync_api import sync_playwright

        acc = account_manager.get_account(self.account_id)
        if not acc:
            self._set("failed", "账号不存在")
            self._done.set()
            return

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    channel="chrome", headless=self.headless,
                    args=["--disable-blink-features=AutomationControlled"])
                ctx = browser.new_context(
                    user_agent=account_manager.UA,
                    viewport={"width": 1280, "height": 900})
                page = ctx.new_page()
                page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(2500)

                # 第 1 关：手机号 + 密码 + 协议
                page.locator("input[placeholder*='手机号']").first.fill(acc["phone"])
                page.locator("input[placeholder*='密码']").first.fill(acc["password"])
                for sel in ("input[type=checkbox]", "label:has-text('我已阅读')"):
                    box = page.locator(sel)
                    if box.count():
                        try:
                            box.first.click(timeout=1500)
                        except Exception:
                            pass
                        break
                self._click_login(page)
                page.wait_for_timeout(2500)

                # 第 2 关：滑块（点获取短信码前后都可能弹）
                self._pass_slider(page)

                # 点「获取短信验证码」，可能再弹一次滑块
                if self._click_send_sms(page):
                    page.wait_for_timeout(3000)
                    self._pass_slider(page)
                    self.need_sms = True
                    self._set("waiting_sms", "请输入手机收到的 6 位短信码")
                else:
                    self._set("verifying", "没有短信码步骤，直接等登录结果")

                # 第 3 关：等人工填码
                if self.need_sms:
                    if not self._sms_event.wait(300) or self._stop:
                        self._set("failed", "等短信码超时或已取消")
                        browser.close()
                        self._done.set()
                        return
                    filled = False
                    for sel in ("input[placeholder*='短信']", "input[placeholder*='验证码']",
                                "input[maxlength='6']", "input[type=tel]"):
                        el = page.locator(sel)
                        if el.count():
                            try:
                                el.first.fill(self._sms, timeout=3000)
                                filled = True
                                self._set("verifying", f"已填入短信码（{sel}）")
                                break
                            except Exception:
                                continue
                    if not filled:
                        self._set("failed", "找不到短信码输入框")
                        browser.close()
                        self._done.set()
                        return
                    clicked = self._click_login(page)
                    self._set("verifying", f"提交短信码，点登录={'成功' if clicked else '失败'}")
                    page.wait_for_timeout(3000)
                    body = page.inner_text("body")[:120].replace("\n", " ")
                    print(f"[登录 {self.account_id}] 提交后页面：{body}", flush=True)

                # 第 4 关：登录时的极验 v4
                self._solve_v4(page)

                # 判定结果
                ok, msg = self._wait_logged_in(page)
                if ok:
                    account_manager.save_state(self.account_id, ctx.storage_state())
                    self._set("ok", msg)
                else:
                    account_manager.set_status(self.account_id, "failed", msg)
                    self._set("failed", msg)
                browser.close()
        except Exception as e:
            self._set("failed", f"异常：{str(e)[:120]}")
        finally:
            self._done.set()

    # ---------------------------------------------------------- 分步动作
    def _click_login(self, page) -> bool:
        for sel in (".btn-login", "button:has-text('登')", "a:has-text('登录')"):
            el = page.locator(sel)
            if el.count():
                try:
                    el.first.click(timeout=3000)
                    return True
                except Exception:
                    continue
        return False

    def _click_send_sms(self, page) -> bool:
        for sel in ("text=获取短信验证码", "text=获取验证码", "text=发送验证码",
                    "text=发送短信", "[class*=send-code]", "[class*=getCode]"):
            el = page.locator(sel)
            if el.count():
                try:
                    el.first.click(timeout=3000)
                    return True
                except Exception:
                    continue
        return False

    def _pass_slider(self, page) -> bool:
        """滑块：取图找缺口 + 拟人轨迹，多试几次（v3 时代就是这么过的）。"""
        import slider_solver

        # 网络慢时面板要等一会儿才出来
        for _ in range(20):
            if page.locator("[class*=geetest_panel], [class*=geetest_window], [class*=geetest_canvas]").count():
                break
            page.wait_for_timeout(800)

        def log(msg: str) -> None:
            print(f"[登录-滑块] {msg}", flush=True)

        ok = slider_solver.solve_slider_canvas(page, tries=5, log=log)
        if ok:
            time.sleep(1.0)
        return ok

    def _solve_v4(self, page) -> bool:
        """登录弹的极验 v4 点选：复用同步 solver。"""
        import geetest_solver
        from record_lianjia import (click_confirm, click_points, probe,
                                    refresh_captcha, wait_ready)
        solver = geetest_solver.GeetestSolver()
        for _ in range(3):
            st = wait_ready(page, timeout_s=8)
            if not st:
                return False
            png = page.locator("[class*=geetest_bg]").screenshot()
            res = solver.classification(png)
            pts = res.get("points") or []
            if len(pts) < 3:
                refresh_captcha(page, st)
                continue
            click_points(page, st, pts)
            time.sleep(0.3)
            click_confirm(page)
            time.sleep(2.0)
            if "验证通过" in page.inner_text("body"):
                return True
            refresh_captcha(page, st)
        return False

    def _wait_logged_in(self, page) -> tuple[bool, str]:
        for _ in range(20):
            page.wait_for_timeout(1500)
            url = page.url
            if "clogin" not in url:
                return True, f"已登录：{url[:60]}"
            body = page.inner_text("body")[:300]
            if "密码错误" in body or "验证码错误" in body:
                return False, "账号、密码或短信码不对"
        return False, f"仍在登录页：{page.url[:60]}"


# 会话表：account_id -> LoginSession，供后台轮询
SESSIONS: dict[int, LoginSession] = {}


def start(account_id: int, headless: bool = False) -> dict:
    sess = SESSIONS.get(account_id)
    if sess and sess._thread and sess._thread.is_alive():
        return sess.status()
    sess = LoginSession(account_id, headless=headless)
    SESSIONS[account_id] = sess
    sess.start()
    return sess.status()


def status(account_id: int) -> dict:
    sess = SESSIONS.get(account_id)
    return sess.status() if sess else {"state": "idle", "message": "", "need_sms": False, "done": True}


def submit_sms(account_id: int, code: str) -> dict:
    sess = SESSIONS.get(account_id)
    if not sess:
        return {"ok": False, "message": "没有进行中的登录"}
    ok = sess.submit_sms(code)
    return {"ok": ok, "message": "已提交" if ok else "当前不在等短信码"}

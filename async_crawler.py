"""多账号、多 IP 异步爬取链家列表页。

设计：
    - 账号池来自 account_manager（带 Cookie 的 storage_state）
    - 代理池来自 account_manager.proxies，每个 worker 绑一条
    - 任务 = (城市, 页码)，放进 asyncio.Queue
    - worker：取任务 → 用账号 Cookie 打开页面 → 遇到验证码交给 solver → 提取记录
    - 遇到登录墙：把账号标记过期，任务退回队列换账号重试
    - 代理连续失败三次自动停用

用法：
    python async_crawler.py --cities bj,sh,gz --pages 1-3 \
        --workers 4 --accounts 2 --out data/crawl_async.jsonl

    # 只抓首页、不用账号（匿名验证码照样能过）
    python async_crawler.py --cities bj,sh --pages 1 --workers 3 --anonymous
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import account_manager

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

EXTRACT_JS = r"""
() => {
  const out = [];
  for (const li of document.querySelectorAll('ul.sellListContent > li')) {
    const t = (sel) => { const e = li.querySelector(sel); return e ? e.innerText.trim() : ''; };
    const a = li.querySelector('.title a');
    const rec = {
      title: t('.title'), link: a ? a.href : '',
      total_price: t('.totalPrice'), unit_price: t('.unitPrice'),
      house_info: t('.houseInfo'), position: t('.positionInfo'),
      follow_info: t('.followInfo'), tags: t('.tag'),
    };
    if (rec.title) out.push(rec);
  }
  return out;
}
"""

PROBE_JS = """
() => {
  const bgEl = document.querySelector('[class*=geetest_bg]');
  if (!bgEl) return null;
  const b = bgEl.getBoundingClientRect();
  if (b.width < 200) return null;
  const ques = [];
  for (const im of document.querySelectorAll('img')) {
    const r = im.getBoundingClientRect();
    const s = im.src || '';
    if (r.width < 15 || r.width > 120 || r.height < 15 || r.height > 120) continue;
    if (s.indexOf('geetest') < 0) continue;
    ques.push(s);
  }
  const bg = getComputedStyle(bgEl).backgroundImage || '';
  const g = document.querySelector('[class*=geetest_captcha]');
  const text = g ? g.innerText.replace(/\\n/g, ' ').replace('验证中', ' ') : '';
  return {x:b.x, y:b.y, w:b.width, h:b.height, ques, text, bg};
}
"""

START_BTN_JS = """
() => {
  for (const e of document.querySelectorAll('div,span,a,button')) {
    const t = (e.textContent || '').trim();
    if (t === '点击按钮开始验证' || t === '点击进行验证' || t === '点击开始验证') {
      const r = e.getBoundingClientRect();
      if (r.width > 100) return {x: r.x + r.width / 2, y: r.y + r.height / 2};
    }
  }
  return null;
}
"""


class Stats:
    def __init__(self):
        self.rows = 0
        self.pages = 0
        self.captcha = 0
        self.captcha_ok = 0
        self.login_wall = 0
        self.failed = 0
        self.lock = asyncio.Lock()

    async def add(self, **kw):
        async with self.lock:
            for k, v in kw.items():
                setattr(self, k, getattr(self, k) + v)


async def click_start(page) -> bool:
    """点「点击按钮开始验证」，按钮可能几秒后才出现。"""
    for _ in range(25):
        box = await page.evaluate(START_BTN_JS)
        if box:
            await page.mouse.move(box["x"] - 80, box["y"])
            await page.wait_for_timeout(180)
            await page.mouse.move(box["x"], box["y"], steps=8)
            await page.mouse.down()
            await page.wait_for_timeout(90)
            await page.mouse.up()
            return True
        await page.wait_for_timeout(400)
    return False


def parse_tip(tip: str) -> str:
    """从面板文字判断题型：phrase / word / icon。"""
    s = (tip or "").replace("\n", " ")
    if "按语序" in s:
        return "phrase"
    return "word"          # word 和 icon 的分支一样，都走形状匹配


REFRESH_JS = """
() => {
  for (const e of document.querySelectorAll('div,span,a,button')) {
    const t = (e.textContent || '').trim();
    if (t === '刷新验证' || t === '看不清可刷新' || t === '刷新') {
      const r = e.getBoundingClientRect();
      if (r.width > 20) return {x: r.x + r.width / 2, y: r.y + r.height / 2};
    }
  }
  return null;
}
"""


async def refresh_panel(page) -> None:
    box = await page.evaluate(REFRESH_JS)
    if box:
        await page.mouse.click(box["x"], box["y"])
        await page.wait_for_timeout(1200)


async def wait_panel(page, timeout_s: float = 20.0) -> dict | None:
    """等面板出题，并且位置稳定（面板会在加载后重新居中，位置会跳一次）。"""
    import time as _t
    t0 = _t.time()
    prev = None
    while _t.time() - t0 < timeout_s:
        st = await page.evaluate(PROBE_JS)
        if st:
            text = st.get("text") or ""
            bg = st.get("bg") or ""
            ready = "url(" in bg and (len(st.get("ques") or []) >= 3 or "请" in text)
            if ready:
                if prev and abs(prev["x"] - st["x"]) < 1 and abs(prev["y"] - st["y"]) < 1 \
                        and abs(prev["w"] - st["w"]) < 1:
                    return st
                prev = st
        await page.wait_for_timeout(400)
    return None


async def solve_captcha(page, solver) -> bool:
    """解一次验证码：先核对点数，点数不对就换题，别拿错的点数去提交。"""
    for _ in range(4):
        st = await wait_panel(page)
        if not st:
            print("面板没出题，跳过", flush=True)
            return False

        kind = parse_tip(st.get("text") or "")
        expect = 4 if kind == "phrase" else 3

        png = await page.locator("[class*=geetest_bg]").screenshot()
        ques = None
        if kind in ("word", "icon") and len(st.get("ques") or []) == 3:
            ques = []
            for src in st["ques"]:
                r = await page.request.get(src)
                ques.append(await r.body())

        res = await asyncio.get_running_loop().run_in_executor(
            None, lambda: solver.classification(png, ques))
        pts = res.get("points") or []

        if len(pts) != expect:
            print(f"点数不对：{kind} 应 {expect} 个，实得 {len(pts)} 个，换一题", flush=True)
            await refresh_panel(page)
            continue

        # 面板位置会跳，点之前再取一次
        fresh = await wait_panel(page)
        if fresh:
            st = fresh

        for i, (px, py) in enumerate(pts, 1):
            await page.mouse.move(st["x"] + px - 4, st["y"] + py - 4)
            await page.mouse.down()
            await page.wait_for_timeout(70)
            await page.mouse.up()
            await page.wait_for_timeout(260)

        marks = await page.evaluate("""
        () => document.querySelectorAll('[class*=geetest_tip], [class*=geetest_click]').length
        """)
        if marks and marks < expect:
            print(f"只登记了 {marks} 个点，重来", flush=True)
            await refresh_panel(page)
            continue

        sub = await page.query_selector("[class*=geetest_submit]")
        if sub:
            box = await sub.bounding_box()
            if box:
                await page.mouse.click(box["x"] + box["width"] / 2,
                                       box["y"] + box["height"] / 2)

        for _ in range(10):
            await page.wait_for_timeout(800)
            body = await page.inner_text("body")
            if "验证通过" in body:
                return True
            if "验证失败" in body or "请重试" in body:
                break
        await refresh_panel(page)
    return False


async def worker(name: str, queue: asyncio.Queue, args, stats: Stats, out_path: Path,
                 solver, browser, lock: asyncio.Lock):
    accounts = account_manager.list_accounts() if not args.anonymous else []
    ready = [a for a in accounts if a["status"] == "ok" and a["has_cookie"]]
    proxies = account_manager.list_proxies(enabled_only=True)

    idx = hash(name) % len(ready) if ready else -1
    proxy = proxies[hash(name) % len(proxies)] if proxies else None

    ctx_kw = {"user_agent": UA, "viewport": {"width": 1280, "height": 900}}
    if proxy:
        ctx_kw["proxy"] = {"server": proxy["url"]}
    if idx >= 0:
        st = account_manager.get_state(ready[idx]["id"])
        if st:
            ctx_kw["storage_state"] = st

    ctx = await browser.new_context(**ctx_kw)
    page = await ctx.new_page()

    while True:
        try:
            city, pageno, attempt = queue.get_nowait()
        except asyncio.QueueEmpty:
            break

        url = f"https://{city}.lianjia.com/ershoufang/" + ("" if pageno == 1 else f"pg{pageno}/")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(1500)

            if "clogin" in page.url:
                await stats.add(login_wall=1)
                print(f"[{name}] 登录墙：{city} p{pageno}", flush=True)
                if idx >= 0:
                    account_manager.set_status(ready[idx]["id"], "expired", "爬取时被要求登录")
                if attempt < 2:
                    await queue.put((city, pageno, attempt + 1))
                await asyncio.sleep(1)
                continue

            if "captcha" in page.url or await page.query_selector("[class*=geetest_bg]"):
                await stats.add(captcha=1)
                await click_start(page)
                await page.wait_for_timeout(800)
                if not await wait_panel(page):
                    await stats.add(failed=1)
                    print(f"[{name}] 点了开始但没出题", flush=True)
                    if attempt < 2:
                        await queue.put((city, pageno, attempt + 1))
                    continue
                ok = await solve_captcha(page, solver)
                if ok:
                    await stats.add(captcha_ok=1)
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    await page.wait_for_timeout(1500)
                else:
                    await stats.add(failed=1)
                    if attempt < 2:
                        await queue.put((city, pageno, attempt + 1))
                    continue

            rows = await page.evaluate(EXTRACT_JS)
            if rows:
                async with lock:
                    with out_path.open("a", encoding="utf-8") as fh:
                        for r in rows:
                            r["city"] = city
                            r["page"] = pageno
                            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                await stats.add(rows=len(rows), pages=1)
                print(f"[{name}] {city} p{pageno} → {len(rows)} 条", flush=True)
                if proxy:
                    account_manager.proxy_ok(proxy["id"])
            else:
                await stats.add(failed=1)
                print(f"[{name}] {city} p{pageno} 没取到，URL={page.url[:60]}", flush=True)
        except Exception as e:
            await stats.add(failed=1)
            print(f"[{name}] 出错 {city} p{pageno}: {str(e)[:80]}", flush=True)
            if proxy:
                account_manager.proxy_failed(proxy["id"])
        finally:
            queue.task_done()
            await asyncio.sleep(1.0)

    await ctx.close()


async def run(args) -> Stats:
    from playwright.async_api import async_playwright
    import geetest_solver

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    solver = geetest_solver.GeetestSolver()
    stats = Stats()
    lock = asyncio.Lock()

    queue: asyncio.Queue = asyncio.Queue()
    for city in [c.strip() for c in args.cities.split(",") if c.strip()]:
        a, _, b = args.pages.partition("-")
        lo = int(a)
        hi = int(b) if b else lo
        for n in range(lo, hi + 1):
            queue.put_nowait((city, n, 0))

    total = queue.qsize()
    print(f"任务 {total} 个，worker {args.workers} 个", flush=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=args.headless,
                                          args=["--disable-blink-features=AutomationControlled"])
        tasks = [asyncio.create_task(worker(f"w{i}", queue, args, stats, out_path,
                                            solver, browser, lock))
                 for i in range(args.workers)]
        await asyncio.gather(*tasks)
        await browser.close()

    print(f"完成：{stats.pages} 页，{stats.rows} 条，验证码 {stats.captcha_ok}/{stats.captcha}，"
          f"登录墙 {stats.login_wall}，失败 {stats.failed}", flush=True)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cities", default="bj")
    ap.add_argument("--pages", default="1", help="页码范围，如 1-3")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--accounts", type=int, default=0, help="预留，实际用库里已登录账号")
    ap.add_argument("--anonymous", action="store_true", help="不用账号 Cookie")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--out", default="data/crawl_async.jsonl")
    args = ap.parse_args()
    asyncio.run(run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

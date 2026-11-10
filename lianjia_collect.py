"""链家成交数据抓取：接入极验 v4 求解器，遇到验证码自动通过。

用法：
    python lianjia_collect.py --city bj --pages 2 --out data/chengjiao_bj.jsonl

流程：
    1. 打开 /chengjiao/pg{n}/
    2. 出现人机验证就调用 geetest_solver 解掉（支持 phrase / word / icon）
    3. 从列表提取成交记录，按行写入 jsonl

依赖：playwright；识别代码与模型在本仓库（geetest_solver/ + models/）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from playwright.sync_api import sync_playwright

import geetest_solver
from record_lianjia import (check_result, click_confirm, click_points,
                            click_start, probe, refresh_captcha, wait_ready)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# 从列表页提取成交记录
EXTRACT_JS = r"""
() => {
  const out = [];
  const items = document.querySelectorAll('ul.listContent > li, .listContent li');
  for (const li of items) {
    const t = (sel) => { const e = li.querySelector(sel); return e ? e.innerText.trim() : ''; };
    const a = li.querySelector('.title a');
    const rec = {
      title: t('.title'),
      link: a ? a.href : '',
      total_price: t('.totalPrice .number') || t('.totalPrice'),
      unit_price: t('.unitPrice .number') || t('.unitPrice'),
      deal_date: t('.dealDate'),
      house_info: t('.houseInfo'),
      position: t('.positionInfo'),
      deal_cycle: t('.dealCycleTxt') || '',
    };
    if (rec.title) out.push(rec);
  }
  return out;
}
"""


# 二手房列表：/ershoufang/，公开版块，不需要登录
EXTRACT_ERSHOUFANG_JS = r"""
() => {
  const out = [];
  for (const li of document.querySelectorAll('ul.sellListContent > li')) {
    const t = (sel) => { const e = li.querySelector(sel); return e ? e.innerText.trim() : ''; };
    const a = li.querySelector('.title a');
    const rec = {
      title: t('.title'),
      link: a ? a.href : '',
      total_price: t('.totalPrice'),
      unit_price: t('.unitPrice'),
      house_info: t('.houseInfo'),
      position: t('.positionInfo'),
      follow_info: t('.followInfo'),
      tags: t('.tag'),
    };
    if (rec.title) out.push(rec);
  }
  return out;
}
"""

KINDS = {
    "chengjiao": {"path": "chengjiao", "extract": EXTRACT_JS, "wait": "ul.listContent"},
    "ershoufang": {"path": "ershoufang", "extract": EXTRACT_ERSHOUFANG_JS,
                   "wait": "ul.sellListContent"},
}


def log(msg: str) -> None:
    print(f"[抓取] {msg}", flush=True)


def solve_captcha(page, solver, tries: int = 6) -> bool:
    """在验证码页面里解一次题。返回是否通过。"""
    for attempt in range(1, tries + 1):
        st = wait_ready(page, timeout_s=15)
        if not st:
            log("验证码面板没出现")
            return False
        panel = Path("_cap_panel.png")
        page.locator("[class*=geetest_bg]").screenshot(path=str(panel))
        png = panel.read_bytes()

        ques = []
        for q in st.get("ques") or []:
            try:
                import requests
                ques.append(requests.get(q["src"], timeout=8).content)
            except Exception:
                pass

        res = solver.classification(png, ques if len(ques) == 3 else None)
        kind = res.get("type")
        pts = res.get("points") or []
        log(f"第 {attempt} 次：题型={kind} 点数={len(pts)}")
        if not pts:
            refresh_captcha(page, st)
            continue

        click_points(page, st, pts)
        time.sleep(0.3)
        click_confirm(page)
        time.sleep(1.8)
        if check_result(page) == "success":
            log(f"验证通过（第 {attempt} 次）")
            return True
        refresh_captcha(page, st)
    return False


def needs_captcha(page) -> bool:
    st = probe(page)
    return bool(st.get("ready")) or "验证" in (st.get("text") or "")


def open_list_page(page, url: str, solver, wait_sel: str, tries: int = 3) -> bool:
    """打开列表页，遇到验证码就解，解完再进一次。返回是否拿到列表。"""
    for k in range(1, tries + 1):
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1500)

        if "captcha" in page.url or "人机验证" in page.inner_text("body")[:60]:
            log(f"第 {k} 次遇到人机验证，开始解题")
            click_start(page)
            if not solve_captcha(page, solver):
                log("这一轮没解出来")
                continue
            page.wait_for_timeout(800)
            # 解完还停在验证页，得再请求一次目标地址
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1500)
            if "captcha" in page.url:
                log("解完又被拦，继续下一轮")
                continue

        try:
            page.wait_for_selector(wait_sel, timeout=12000)
            return True
        except Exception:
            log(f"第 {k} 次没等到列表，URL={page.url[:60]}")
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="bj", help="城市代码，逗号分隔可跑多个")
    ap.add_argument("--kind", default="ershoufang", choices=sorted(KINDS))
    ap.add_argument("--pages", default="1", help="页码，支持 2 或 2-4")
    ap.add_argument("--out", default="data/chengjiao.jsonl")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--account", type=int, default=0, help="用哪个账号的 Cookie")
    args = ap.parse_args()

    solver = geetest_solver.GeetestSolver()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    total = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=args.headless,
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx_kw = {"user_agent": UA, "viewport": {"width": 1280, "height": 900}}
        if args.account:
            import account_manager as _am
            st = _am.get_state(args.account)
            if st:
                ctx_kw["storage_state"] = st
                log(f"使用账号 {args.account} 的 Cookie")
        ctx = browser.new_context(**ctx_kw)
        page = ctx.new_page()

        a, _, b = str(args.pages).partition("-")
        lo, hi = int(a), int(b) if b else int(a)
        for city in [c.strip() for c in args.city.split(",") if c.strip()]:
            for n in range(lo, hi + 1):
                cfg = KINDS[args.kind]
                url = (f"https://{city}.lianjia.com/{cfg['path']}/"
                       + ("" if n == 1 else f"pg{n}/"))
                log(f"打开 {url}")
                if not open_list_page(page, url, solver, cfg["wait"]):
                    log("这一页没进去，跳过")
                    continue

                rows = page.evaluate(cfg["extract"])
                log(f"{city} 第 {n} 页取到 {len(rows)} 条")
                with out.open("a", encoding="utf-8") as fh:
                    for r in rows:
                        r["city"] = city
                        r["page"] = n
                        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                total += len(rows)
                page.wait_for_timeout(1500)

        browser.close()

    log(f"合计 {total} 条，写入 {out}")
    return 0 if total else 1


if __name__ == "__main__":
    raise SystemExit(main())

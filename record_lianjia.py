"""
链家真实验证码录制：从触发出题到点击通过，全程录屏

要点
----
1. 用本机 Chrome（完整指纹）
2. 快速请求链家列表页触发人机验证
3. 点击「点击按钮开始验证」，等极验出题
4. **等图片真正加载完**（背景图 URL 稳定 + 面板尺寸不再变化）
5. 截取验证码区域 → solver 求解 → 真实鼠标点击
6. 全程 Playwright 录屏

输出
----
    docs/video/lianjia_demo.webm / .mp4
    docs/video/frames/*.png
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE))

OUT = HERE / "docs" / "video"
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "frames").mkdir(exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

URLS = [
    "https://bj.lianjia.com/chengjiao/",
    "https://bj.lianjia.com/chengjiao/pg{n}/",
    "https://sh.lianjia.com/chengjiao/pg{n}/",
    "https://gz.lianjia.com/chengjiao/pg{n}/",
    "https://bj.lianjia.com/xiaoqu/pg{n}/",
    "https://bj.lianjia.com/zufang/pg{n}/",
    "https://cd.lianjia.com/chengjiao/pg{n}/",
    "https://hz.lianjia.com/chengjiao/",
]


def log(m: str) -> None:
    print(f"[录屏] {m}", flush=True)


# ---------------------------------------------------------------- DOM 探测
PROBE_JS = """
() => {
    const r = {ready:false, x:0, y:0, w:0, h:0, bg:'', ques:[], text:'', submit:null};
    // 背景图：.geetest_bg
    const bgEl = document.querySelector('[class*=geetest_bg]');
    if (bgEl) {
        const b = bgEl.getBoundingClientRect();
        const cs = getComputedStyle(bgEl);
        if (b.width > 200 && b.height > 100) {
            r.x = b.x; r.y = b.y; r.w = b.width; r.h = b.height;
            r.bg = cs.backgroundImage || '';
            r.ready = r.bg !== '' && r.bg !== 'none';
        }
    }
    // ques 小图：只取 geetest 域名的（跳过页面 logo / 备案图标）
    for (const im of document.querySelectorAll('img')) {
        const b = im.getBoundingClientRect();
        const s = im.src || '';
        if (b.width < 15 || b.width > 120 || b.height < 15 || b.height > 120) continue;
        if (s.indexOf('static.geetest.com') < 0 && s.indexOf('geetest') < 0) continue;
        r.ques.push({x:b.x, y:b.y, w:b.width, h:b.height, src:s});
    }
    // 确定按钮
    const sub = document.querySelector('[class*=geetest_submit]');
    if (sub) { const b = sub.getBoundingClientRect();
        r.submit = {x:b.x, y:b.y, w:b.width, h:b.height}; }
    const g = document.querySelector('[class*=geetest_captcha]');
    r.text = g ? g.innerText.replace(/\\n/g, '|').slice(0, 80) : '';
    return r;
}
"""


def probe(page) -> dict:
    try:
        return page.evaluate(PROBE_JS)
    except Exception:
        return {"kind": None, "text": ""}


def wait_ready(page, timeout_s: float = 20.0) -> dict | None:
    """等背景图加载完 + 面板稳定（连续 3 次 bg 和尺寸不变）。"""
    t0 = time.time()
    last = None
    stable = 0
    while time.time() - t0 < timeout_s:
        page.wait_for_timeout(320)
        st = probe(page)
        if not st.get("ready"):
            stable = 0
            last = None
            continue
        # 有 ques 的题型要求 3 张都到齐
        if len(st.get("ques") or []) < 3 and "点击" in (st.get("text") or ""):
            stable = 0
            last = None
            continue
        key = (round(st["w"]), round(st["h"]), str(st.get("bg"))[:150],
               len(st.get("ques") or []))
        if key == last:
            stable += 1
            if stable >= 3:
                return st
        else:
            stable = 0
            last = key
    return None


def trigger(page, max_req: int = 55) -> bool:
    """快速请求直到出现人机验证页。"""
    for i in range(max_req):
        u = URLS[i % len(URLS)].format(n=i + 1)
        try:
            page.goto(u, timeout=6000, wait_until="commit")
        except Exception:
            pass
        page.wait_for_timeout(180)
        try:
            if page.locator("text=人机验证").count() > 0:
                return True
        except Exception:
            pass
    return False


def click_start(page, wait_s: float = 12.0) -> None:
    """点「点击按钮开始验证」，带悬停。

    验证码页会先显示「加载中，请稍后...」，按钮可能几秒后才出现，
    所以这里轮询等待，而不是等固定 500ms 就瞎点。
    """
    box = None
    t0 = time.time()
    while time.time() - t0 < wait_s:
        box = page.evaluate("""() => {
            for (const e of document.querySelectorAll('div,span,a,button')) {
                const t = (e.textContent || '').trim();
                if (t === '点击按钮开始验证' || t === '请依次点击' ||
                    t === '点击进行验证' || t === '点击开始验证') {
                    const r = e.getBoundingClientRect();
                    if (r.width > 100) {
                        return {x:r.x, y:r.y, w:r.width, h:r.height, t:t};
                    }
                }
            }
            return null;
        }""")
        if box:
            log(f"找到开始按钮「{box.get('t')}」")
            break
        page.wait_for_timeout(400)
    if not box:
        log("等不到开始按钮，看是否已直接出题")
        return
    cx, cy = box["x"] + box["w"] / 2, box["y"] + box["h"] / 2
    page.mouse.move(cx - 90, cy, steps=10)
    page.wait_for_timeout(220)
    page.mouse.move(cx, cy, steps=12)
    page.wait_for_timeout(320)
    page.mouse.down()
    page.wait_for_timeout(110)
    page.mouse.up()
    log(f"点击开始按钮 ({cx:.0f},{cy:.0f})")


def save_clip(page, st: dict, name: str) -> bytes:
    clip = {"x": max(0, st["x"]), "y": max(0, st["y"]),
            "width": st["w"], "height": st["h"]}
    png = page.screenshot(clip=clip, type="png")
    (OUT / "frames" / name).write_bytes(png)
    return png


def click_points(page, st: dict, pts: list) -> None:
    for i, (px, py) in enumerate(pts):
        x = st["x"] + px
        y = st["y"] + py
        page.mouse.move(x - 4, y - 4, steps=6)
        page.wait_for_timeout(70)
        page.mouse.move(x, y, steps=5)
        page.wait_for_timeout(90)
        page.mouse.down()
        page.wait_for_timeout(65)
        page.mouse.up()
        log(f"  点击 {i + 1}/{len(pts)} ({px:.0f},{py:.0f})")
        page.wait_for_timeout(300)


def click_confirm(page) -> bool:
    """点「确定」按钮（.geetest_submit）。"""
    box = page.evaluate("""() => {
        const s = document.querySelector('[class*=geetest_submit]');
        if (s) { const r = s.getBoundingClientRect();
            if (r.width > 50) return {x:r.x, y:r.y, w:r.width, h:r.height}; }
        for (const e of document.querySelectorAll('div,span,a,button')) {
            const t = (e.textContent || '').trim();
            if (t === '确定' || t === '确认') {
                const r = e.getBoundingClientRect();
                if (r.width > 80) return {x:r.x, y:r.y, w:r.width, h:r.height};
            }
        }
        return null;
    }""")
    if not box:
        return False
    cx, cy = box["x"] + box["w"] / 2, box["y"] + box["h"] / 2
    page.mouse.move(cx, cy, steps=8)
    page.wait_for_timeout(140)
    page.mouse.click(cx, cy)
    log("点击确定")
    return True


def check_result(page) -> str:
    """返回 success / fail / unknown"""
    page.wait_for_timeout(2000)
    # 页面是否已回到链家（说明验证通过）
    try:
        url = page.url
        has_geetest = page.locator("[class*=geetest_captcha]").count() > 0
        body = page.evaluate("() => document.body.innerText.slice(0, 400)")
    except Exception:
        return "unknown"
    for kw in ("验证通过", "验证成功"):
        if kw in body:
            return "success"
    for kw in ("验证失败", "验证错误", "请重试", "重试"):
        if kw in body:
            return "fail"
    if "人机验证" not in body and not has_geetest:
        return "success"
    return "unknown"



def refresh_captcha(page, st: dict | None) -> bool:
    """点验证码面板上的刷新按钮；找不到就整页重载再触发。"""
    box = page.evaluate("""() => {
        for (const e of document.querySelectorAll('[class*=refresh],[class*=geetest_refresh]')) {
            const r = e.getBoundingClientRect();
            if (r.width >= 10 && r.height >= 10) {
                return {x:r.x + r.width/2, y:r.y + r.height/2};
            }
        }
        return null;
    }""")
    if box:
        page.mouse.click(box["x"], box["y"])
        log(f"点刷新按钮 ({box['x']:.0f},{box['y']:.0f})")
        page.wait_for_timeout(1200)
        return True
    log("找不到刷新按钮，整页重载")
    try:
        page.reload(timeout=8000)
    except Exception:
        pass
    page.wait_for_timeout(800)
    return trigger(page)


def clip_hash(png: bytes) -> str:
    import hashlib
    return hashlib.md5(png).hexdigest()


def wait_new_image(page, st_prev_hash: str, timeout_s: float = 8.0):
    """等验证码图片真的换成新的（内容 hash 变了）。"""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        st = wait_ready(page, 2.0)
        if st:
            png = save_clip(page, st, "_tmp_wait.png")
            if clip_hash(png) != st_prev_hash:
                return st, png
        page.wait_for_timeout(300)
    return None, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", default="false")
    ap.add_argument("--trials", type=int, default=6)
    ap.add_argument("--no-cut", action="store_true",
                    help="不裁剪，保留整段视频")
    ap.add_argument("--lead", type=float, default=1.0,
                    help="成功那一次开始前多留几秒")
    ap.add_argument("--tail", type=float, default=2.5,
                    help="通过之后多留几秒")
    ap.add_argument("--only-phrase", action="store_true",
                    help="只做 phrase 型（无 ques 小图），word/icon 直接换题")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    chrome = next((p for p in CHROME_PATHS if Path(p).exists()), None)
    if chrome:
        log(f"Chrome: {chrome}")
    headless = args.headless.lower() != "false"
    ok = False
    solved_any = False

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            executable_path=chrome,
            args=["--disable-blink-features=AutomationControlled",
                  "--disable-features=IsolateOrigins,site-per-process"],
        )
        ctx = browser.new_context(
            user_agent=UA, viewport={"width": 1280, "height": 800},
            locale="zh-CN",
            record_video_dir=str(OUT),
            record_video_size={"width": 1280, "height": 800},
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = ctx.new_page()
        video_start = time.monotonic()      # 录屏起点，用来算裁剪时间
        log("浏览器已启动")

        try:
            if not trigger(page):
                log("未能触发人机验证")
                ctx.close()
                browser.close()
                return 1
            log("已进入人机验证页")
            page.screenshot(path=str(OUT / "frames" / "01_human_check.png"))

            from geetest_solver import GeetestSolver
            solver = GeetestSolver()

            ok_span = None       # (开始求解时刻, 通过时刻)
            for trial in range(1, args.trials + 1):
                log(f"--- 第 {trial} 次 ---")
                t_attempt = time.monotonic()
                click_start(page)
                st = wait_ready(page, 20.0)
                if not st:
                    log("等不到验证码图片")
                    page.screenshot(path=str(OUT / "frames" / f"02_noimg_{trial}.png"))
                    continue
                log(f"验证码就绪 {int(st['w'])}x{int(st['h'])} "
                    f"ques={len(st.get('ques') or [])} 文本={st['text']!r}")
                png = save_clip(page, st, f"03_captcha_{trial}.png")

                # ques 小图
                ques_bytes = []
                for q in (st.get("ques") or [])[:3]:
                    try:
                        ques_bytes.append(page.screenshot(
                            clip={"x": q["x"], "y": q["y"],
                                  "width": q["w"], "height": q["h"]}, type="png"))
                    except Exception:
                        pass
                log(f"ques 小图 {len(ques_bytes)} 张")

                if args.only_phrase and ques_bytes:
                    log("  这题是 word/icon（有 ques 小图），跳过换题")
                    refresh_captcha(page, st)
                    page.wait_for_timeout(700)
                    continue

                res = solver.classification(png, ques_bytes or None)
                pts = res.get("points") or []
                log(f"求解 类型={res.get('type')} 词={res.get('word')} 点数={len(pts)}")
                if not pts:
                    log(f"  失败: {res.get('error')}")
                    page.screenshot(path=str(OUT / "frames" / f"04_solvefail_{trial}.png"))
                    refresh_captcha(page, st)
                    page.wait_for_timeout(600)
                    continue

                solved_any = True
                click_points(page, st, pts)
                page.screenshot(path=str(OUT / "frames" / f"05_clicked_{trial}.png"))
                click_confirm(page)
                r = check_result(page)
                page.screenshot(path=str(OUT / "frames" / f"06_result_{trial}_{r}.png"))
                log(f"结果: {r}")
                if r == "success":
                    ok = True
                    ok_span = (t_attempt, time.monotonic())
                    page.wait_for_timeout(1500)
                    page.screenshot(path=str(OUT / "frames" / "07_success.png"))
                    break
                # 失败：换一张新题再试（同一张图重复解没有意义）
                log("  刷新题目")
                refresh_captcha(page, st)
                new_st, new_png = wait_new_image(page, clip_hash(png), 6.0)
                if not new_st:
                    log("  题目没换，重载页面重新触发")
                    try:
                        page.reload(timeout=8000)
                    except Exception:
                        pass
                    page.wait_for_timeout(800)
                    trigger(page)
                page.wait_for_timeout(400)

        finally:
            page.wait_for_timeout(1200)
            ctx.close()
            browser.close()

    vids = sorted(OUT.glob("*.webm"), key=lambda p: p.stat().st_mtime, reverse=True)
    if vids:
        src = vids[0]
        dst = OUT / "lianjia_demo.webm"
        if src != dst:
            if dst.exists():
                dst.unlink()
            src.replace(dst)
        log(f"视频 {dst.name} {dst.stat().st_size // 1024} KB")
        # ---- 只保留「成功通过」那一段 ----
        if ok and ok_span and not args.no_cut:
            ss = max(0.0, (ok_span[0] - video_start) - args.lead)
            to = (ok_span[1] - video_start) + args.tail
            import shutil as _sh
            import subprocess as _sp
            ff = (_sh.which("ffmpeg")
                  or r"C:\ProgramData\chocolatey\lib\ffmpeg\tools\ffmpeg\bin\ffmpeg.exe")
            if Path(ff).exists():
                clip = OUT / "lianjia_pass.webm"
                _sp.run([ff, "-y", "-ss", f"{ss:.2f}", "-to", f"{to:.2f}",
                         "-i", str(dst), "-c", "copy", str(clip)],
                        capture_output=True)
                if clip.exists() and clip.stat().st_size > 1024:
                    log(f"裁剪成功段 {ss:.1f}s → {to:.1f}s "
                        f"（{clip.stat().st_size // 1024} KB）")
                    dst = clip
        # 转 mp4
        import shutil
        import subprocess
        ff = (shutil.which("ffmpeg")
              or r"C:\ProgramData\chocolatey\lib\ffmpeg\tools\ffmpeg\bin\ffmpeg.exe")
        if Path(ff).exists():
            mp4 = OUT / ("lianjia_pass.mp4" if dst.name == "lianjia_pass.webm"
                         else "lianjia_demo.mp4")
            subprocess.run([ff, "-y", "-i", str(dst), "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", "-crf", "23", str(mp4)],
                           capture_output=True)
            if mp4.exists():
                log(f"MP4 {mp4.name} {mp4.stat().st_size // 1024} KB")
    log(f"求解成功过: {solved_any}  验证通过: {ok}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

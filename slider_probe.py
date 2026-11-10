"""滑块试探：反复尝试，每次保存底图、块图、算出的位移和结果。

目的不是一次通过，而是攒数据：
    data/slider_log/<时间戳>/
        00_bg.png        带缺口的底图
        01_slice.png     拼图块（带 alpha）
        02_curve.json    本次用的位移、匹配分数、轨迹点数
        03_after.png     拖动后的页面
        result.txt       页面反馈文字

用法：
    python slider_probe.py --tries 12
"""

from __future__ import annotations

import argparse
import base64
import json
import random
import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "geetest-captcha-solver"))

from playwright.sync_api import sync_playwright

import account_manager as am

LOGIN_URL = ("https://clogin.lianjia.com/login"
             "?service=https%3A%2F%2Fwww.lianjia.com%2Fuser%2Fcenter%2F")
OUT = HERE / "data" / "slider_log"

GET_CANVAS_JS = """
() => {
  const g = s => { const c = document.querySelector(s); return c ? c.toDataURL('image/png') : null; };
  return {bg: g('.geetest_canvas_bg'), slice: g('.geetest_canvas_slice')};
}
"""

REFRESH_JS = """
() => {
  for (const sel of ['.geetest_refresh_1', '.geetest_refresh']) {
    const e = document.querySelector(sel);
    if (e) { const r = e.getBoundingClientRect(); return {x: r.x + r.width/2, y: r.y + r.height/2}; }
  }
  return null;
}
"""

HANDLE_JS = """
() => {
  const b = document.querySelector('.geetest_slider_button');
  if (!b) return null;
  const r = b.getBoundingClientRect();
  return {x: r.x, y: r.y, w: r.width, h: r.height};
}
"""


def to_img(data_url: str):
    raw = base64.b64decode(data_url.split(",", 1)[1])
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)


def piece_from_slice(sl: np.ndarray):
    """从 slice 里取出拼图块：连通块 + bbox。"""
    alpha = sl[:, :, 3] if sl.ndim == 3 and sl.shape[2] == 4 else None
    if alpha is None or alpha.max() == 0:
        gray = cv2.cvtColor(sl[:, :, :3], cv2.COLOR_BGR2GRAY)
        alpha = (gray > 8).astype(np.uint8) * 255

    m = (alpha > 40).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1:
        return None
    idx = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h, area = stats[idx]
    if area < 200:
        return None
    mask = ((lab == idx).astype(np.uint8)) * 255
    return x, y, w, h, mask, sl[:, :, :3]


def find_gap(bg: np.ndarray, piece) -> tuple[int | None, dict]:
    """多种匹配各算一次，返回最可信的位移和中间数据。"""
    x0, y0, w, h, mask, rgb = piece
    base = bg[:, :, :3]
    info: dict = {"piece": [int(x0), int(y0), int(w), int(h)], "methods": {}}

    def _score(res, label, min_score):
        res = res.copy()
        # 屏蔽拼图块自己所在位置
        res[:, max(0, x0 - w // 2):x0 + w + w // 2] = -1
        _, sc, _, loc = cv2.minMaxLoc(res)
        info["methods"][label] = {"x": int(loc[0]), "score": round(float(sc), 3)}
        return int(loc[0]) if sc >= min_score else None

    # 1) 彩色 + 掩码
    res1 = cv2.matchTemplate(base, rgb[y0:y0 + h, x0:x0 + w], cv2.TM_CCORR_NORMED,
                             mask=mask[y0:y0 + h, x0:x0 + w])
    g1 = _score(res1, "ccorr_color", 0.85)

    # 2) 灰度 + 掩码
    bgg = cv2.cvtColor(base, cv2.COLOR_BGR2GRAY)
    pcg = cv2.cvtColor(rgb[y0:y0 + h, x0:x0 + w], cv2.COLOR_BGR2GRAY)
    res2 = cv2.matchTemplate(bgg, pcg, cv2.TM_CCORR_NORMED,
                             mask=mask[y0:y0 + h, x0:x0 + w])
    g2 = _score(res2, "ccorr_gray", 0.85)

    # 3) 边缘 + 掩码
    e_bg = cv2.Canny(bgg, 50, 150).astype(np.float32)
    e_p = cv2.Canny(mask[y0:y0 + h, x0:x0 + w], 50, 150).astype(np.float32)
    g3 = None
    if e_p.sum() > 50:
        res3 = cv2.matchTemplate(e_bg, e_p, cv2.TM_CCORR_NORMED, mask=e_p)
        g3 = _score(res3, "edge", 0.5)

    # 优先边缘法（对色彩变化不敏感），其次灰度，最后彩色
    gap = next((v for v in (g3, g2, g1) if v is not None), None)
    return gap, info


def human_track(distance: int) -> list[tuple[float, float]]:
    track, remain, v, t = [], float(distance), 0.0, 0.0
    while remain > 0.5:
        if t < 0.35 * max(distance, 1) / 6:
            v = min(v + random.uniform(1.2, 2.6), 9)
        elif remain < 20:
            v = max(v - random.uniform(0.8, 1.8), 1.2)
        else:
            v = random.uniform(4.5, 7.5)
        step = min(v, remain)
        track.append((step, random.uniform(0.012, 0.022)))
        remain -= step
        t += 1
    if len(track) > 6:
        back = -min(3.0, distance * 0.02)
        track += [(back, random.uniform(0.03, 0.05)), (-back, random.uniform(0.03, 0.05))]
    return track


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tries", type=int, default=12)
    args = ap.parse_args()

    acc = am.get_account(2)
    stamp = time.strftime("%m%d_%H%M%S")
    run_dir = OUT / stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=False,
                              args=["--disable-blink-features=AutomationControlled"])
        ctx = b.new_context(user_agent=am.UA, viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        page.locator("input[placeholder*='手机号']").first.fill(acc["phone"])
        page.locator("input[placeholder*='密码']").first.fill(acc["password"])
        try:
            page.locator(".protocol-text").first.click(timeout=2000)
        except Exception:
            pass
        page.locator(".btn-login").first.click()
        page.wait_for_timeout(2500)
        if page.locator("text=获取验证码").count():
            page.locator("text=获取验证码").first.click()
        page.wait_for_timeout(4000)

        for i in range(1, args.tries + 1):
            d = page.evaluate(GET_CANVAS_JS)
            if not d.get("bg") or not d.get("slice"):
                print(f"[{i}] 没拿到 canvas，等 1.5 秒", flush=True)
                page.wait_for_timeout(1500)
                continue
            bg, sl = to_img(d["bg"]), to_img(d["slice"])
            piece = piece_from_slice(sl)
            if not piece:
                print(f"[{i}] slice 里没有块", flush=True)
                page.wait_for_timeout(1200)
                continue

            gap, info = find_gap(bg, piece)
            handle = page.evaluate(HANDLE_JS)
            if gap is None or not handle:
                print(f"[{i}] 没算出缺口 或 没手柄：{info['methods']}", flush=True)
                page.wait_for_timeout(1200)
                continue

            box = page.locator("[class*=geetest_window]").first.bounding_box()
            scale = box["width"] / bg.shape[1] if box else 1.0
            distance = int((gap - piece[0]) * scale)
            info.update({"gap_x": gap, "distance": distance, "scale": round(scale, 3),
                         "handle": {k: round(v, 1) for k, v in handle.items()}})

            adir = run_dir / f"try_{i:02d}"
            adir.mkdir(exist_ok=True)
            cv2.imwrite(str(adir / "00_bg.png"), bg)
            cv2.imwrite(str(adir / "01_slice.png"), sl)
            def _plain(o):
                if isinstance(o, dict):
                    return {k: _plain(v) for k, v in o.items()}
                if isinstance(o, (list, tuple)):
                    return [_plain(v) for v in o]
                if hasattr(o, "item"):
                    return o.item()
                return o
            (adir / "02_curve.json").write_text(
                json.dumps(_plain(info), ensure_ascii=False, indent=2))

            x0 = handle["x"] + handle["w"] / 2
            y0 = handle["y"] + handle["h"] / 2
            # 先把鼠标从别处移过来，别直接出现在手柄上
            page.mouse.move(x0 - random.uniform(120, 220), y0 + random.uniform(-60, 60))
            time.sleep(random.uniform(0.1, 0.2))
            for k in range(6):
                page.mouse.move(x0 - (5 - k) * 4 + random.uniform(-2, 2),
                                y0 + random.uniform(-3, 3))
                time.sleep(random.uniform(0.03, 0.06))
            page.mouse.move(x0, y0)
            time.sleep(random.uniform(0.08, 0.15))
            page.mouse.down()
            time.sleep(random.uniform(0.15, 0.28))

            # 分段：加速 → 匀速 → 减速，中途停顿两次，末端略微越过再回
            remain = distance
            cur = x0
            seg = 0
            while remain > 0.5:
                seg += 1
                ratio = 1 - remain / max(distance, 1)
                if ratio < 0.3:
                    step = min(remain, random.uniform(8, 14))
                elif ratio < 0.75:
                    step = min(remain, random.uniform(5, 9))
                else:
                    step = min(remain, random.uniform(1.5, 4))
                cur += step
                remain -= step
                page.mouse.move(cur, y0 + random.uniform(-1.2, 1.2))
                time.sleep(random.uniform(0.010, 0.026))
                if seg in (4, 9) and remain > 20:      # 中途停一下
                    time.sleep(random.uniform(0.06, 0.12))
            # 越一点点再回来
            over = random.uniform(1.5, 3.5)
            page.mouse.move(x0 + distance + over, y0 + random.uniform(-1, 1))
            time.sleep(random.uniform(0.05, 0.1))
            page.mouse.move(x0 + distance, y0)
            time.sleep(random.uniform(0.18, 0.35))
            page.mouse.up()
            page.wait_for_timeout(2200)

            body = page.inner_text("body")
            ok = "验证通过" in body
            if not ok:
                try:
                    ok = page.locator("[class*=geetest_panel_success]").first.is_visible()
                except Exception:
                    ok = False
            # 回读一次，看块是不是真的到了缺口位置
            d2 = page.evaluate(GET_CANVAS_JS)
            moved = None
            if d2.get("slice"):
                sl2 = to_img(d2["slice"])
                p2 = piece_from_slice(sl2)
                if p2:
                    moved = p2[0]
            info["piece_after"] = moved
            info["target_gap"] = gap
            def _plain(o):
                if isinstance(o, dict):
                    return {k: _plain(v) for k, v in o.items()}
                if isinstance(o, (list, tuple)):
                    return [_plain(v) for v in o]
                if hasattr(o, "item"):
                    return o.item()
                return o
            (adir / "02_curve.json").write_text(
                json.dumps(_plain(info), ensure_ascii=False, indent=2))
            (adir / "result.txt").write_text(("PASS" if ok else "FAIL") + "\n" + body[:300], encoding="utf-8")
            print(f"[{i}] 位移 {distance}px 方法 {info['methods']} 块落点 {moved} 目标 {gap} → {'通过' if ok else '未过'}", flush=True)
            if ok:
                print("通过，保存 Cookie", flush=True)
                am.save_state(2, ctx.storage_state())
                break
            r = page.evaluate(REFRESH_JS)
            if r:
                page.mouse.click(r["x"], r["y"])
                page.wait_for_timeout(2000)
        b.close()
    print("日志目录:", run_dir, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

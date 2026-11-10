"""滑块验证码：不用训练模型，靠图像配准找缺口 + 拟人轨迹拖动。

两种情形
--------
1. 页面同时给了底图和缺口块（geetest_bg / geetest_slice_bg）
   → 用模板匹配（Canny 边缘上做 TM_CCOEFF_NORMED），峰值的 x 就是缺口位置
2. 只有一张带缺口阴影的底图
   → 边缘图上按列统计竖直边缘强度，取最右的强峰（缺口左缘）

轨迹
----
先快后慢：前 40% 走 60% 距离，中段匀速，末段减速并带 1~2 次微小回拉，
最后停 100~200ms 再松手。极验这类会记录时间戳，等速直线基本必挂。

用法
----
    from slider_solver import solve_slider
    ok = solve_slider(page, handle_selector=".geetest_slider_button")
"""

from __future__ import annotations

import random
import time

import cv2
import numpy as np


def _decode(b: bytes) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_COLOR)


def _edges(img: np.ndarray) -> np.ndarray:
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    g = cv2.GaussianBlur(g, (3, 3), 0)
    return cv2.Canny(g, 60, 180)


def gap_by_template(bg: np.ndarray, piece: np.ndarray) -> int | None:
    """缺口块在底图里的 x 偏移。piece 是带 alpha 或纯色底的小图。"""
    e_bg, e_p = _edges(bg), _edges(piece)
    if e_p.shape[0] > e_bg.shape[0] or e_p.shape[1] > e_bg.shape[1]:
        return None
    res = cv2.matchTemplate(e_bg, e_p, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    if score < 0.25:            # 匹配不可信
        return None
    return int(loc[0])


def gap_by_columns(bg: np.ndarray, search: tuple[float, float] = (0.15, 0.92)) -> int | None:
    """只有一张图时：找竖直边缘最集中的一列（缺口左缘）。"""
    e = _edges(bg)
    h, w = e.shape
    col = (e > 0).sum(axis=0).astype(np.float32)
    lo, hi = int(w * search[0]), int(w * search[1])
    if hi - lo < 10:
        return None
    seg = col[lo:hi]
    # 平滑后取峰
    k = np.ones(5, np.float32) / 5
    seg = np.convolve(seg, k, mode="same")
    peak = int(np.argmax(seg))
    if seg[peak] < 6:           # 没有明显竖直边缘
        return None
    return lo + peak


def human_track(distance: int) -> list[tuple[float, float]]:
    """生成 (dx, dt) 轨迹：先快后慢，带一次回拉。"""
    track: list[tuple[float, float]] = []
    remain = float(distance)
    v = 0.0
    t_total = 0.0
    while remain > 0.5:
        # 前段加速，后段减速
        if t_total < 0.35 * distance / 6:
            v = min(v + random.uniform(1.2, 2.6), 9)
        elif remain < 20:
            v = max(v - random.uniform(0.8, 1.8), 1.2)
        else:
            v = random.uniform(4.5, 7.5)
        step = min(v, remain)
        track.append((step, random.uniform(0.012, 0.022)))
        remain -= step
        t_total += 1

    # 一次小幅回拉再补回，模拟手抖
    if len(track) > 6:
        back = -min(3.0, distance * 0.02)
        track.append((back, random.uniform(0.03, 0.05)))
        track.append((-back, random.uniform(0.03, 0.05)))
    return track


def drag(page, handle_selector: str, distance: int) -> None:
    box = page.locator(handle_selector).first.bounding_box()
    if not box:
        raise RuntimeError("找不到滑块手柄")
    x0, y0 = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x0, y0)
    page.mouse.down()
    time.sleep(random.uniform(0.08, 0.15))

    cur = x0
    for dx, dt in human_track(distance):
        cur += dx
        page.mouse.move(cur, y0 + random.uniform(-1.2, 1.2))
        time.sleep(dt)
    page.mouse.move(x0 + distance, y0)
    time.sleep(random.uniform(0.10, 0.22))
    page.mouse.up()


def solve_slider(page, handle_selector: str = ".geetest_slider_button",
                 bg_selector: str = "[class*=geetest_bg]",
                 piece_selector: str = "[class*=geetest_slice_bg]") -> bool:
    """找缺口 → 拖过去。返回是否执行了拖动（不代表一定通过）。"""
    bg_el = page.locator(bg_selector)
    if not bg_el.count():
        return False
    bg = _decode(bg_el.first.screenshot())

    dist = None
    if page.locator(piece_selector).count():
        piece = _decode(page.locator(piece_selector).first.screenshot())
        dist = gap_by_template(bg, piece)
    if dist is None:
        dist = gap_by_columns(bg)
    if dist is None:
        return False

    # 手柄本身有宽度，缺口位置要减去手柄初始偏移
    handle = page.locator(handle_selector).first.bounding_box()
    offset = handle["width"] / 2 if handle else 0
    distance = max(8, int(dist - offset))
    drag(page, handle_selector, distance)
    time.sleep(1.2)
    return True

# ---------------------------------------------------------------- 通用版：自动找图 + 多试
GET_IMAGES_JS = """
() => {
  const out = [];
  for (const e of document.querySelectorAll('div,img,canvas')) {
    const r = e.getBoundingClientRect();
    if (r.width < 150 || r.width > 400 || r.height < 80 || r.height > 260) continue;
    const cs = getComputedStyle(e);
    let src = cs.backgroundImage || '';
    src = src.replace(/^url\(["']?/, '').replace(/["']?\)$/, '');
    if (e.tagName === 'IMG') src = e.src;
    if (src && !src.startsWith('data:')) out.push({cls: e.className.toString().slice(0,60), src, w: Math.round(r.width), h: Math.round(r.height)});
  }
  return out;
}
"""

HANDLE_JS = """
() => {
  const btn = document.querySelector('.geetest_slider_button, .geetest_btn');
  if (btn) { const r = btn.getBoundingClientRect();
    if (r.width > 10) return {x: r.x, y: r.y, w: r.width, h: r.height}; }
  for (const e of document.querySelectorAll('div,span,a')) {
    const t = (e.textContent || '').trim();
    if (t.includes('拖动滑块') || t.includes('向右拖动')) {
      const r = e.getBoundingClientRect();
      if (r.width > 20 && r.height > 20) return {x: r.x, y: r.y, w: r.width, h: r.height};
    }
  }
  for (const sel of ['.geetest_slider_button', '.geetest_btn', '[class*=slider_button]']) {
    const e = document.querySelector(sel);
    if (e) { const r = e.getBoundingClientRect();
      if (r.width > 10) return {x: r.x, y: r.y, w: r.width, h: r.height}; }
  }
  return null;
}
"""

REFRESH_JS = """
() => {
  for (const sel of ['.geetest_refresh_1', '.geetest_refresh', '[class*=refresh]']) {
    const e = document.querySelector(sel);
    if (e) { const r = e.getBoundingClientRect(); return {x: r.x + r.width/2, y: r.y + r.height/2}; }
  }
  return null;
}
"""


def _get(url: str, session) -> bytes | None:
    try:
        r = session.get(url, timeout=10)
        return r.content if r.ok else None
    except Exception:
        return None


def solve_slider_retry(page, tries: int = 6, log=print) -> bool:
    """取图 → 找缺口 → 拖 → 看结果；失败就刷新再来。"""
    import requests

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": page.url})

    for attempt in range(1, tries + 1):
        imgs = page.evaluate(GET_IMAGES_JS)
        if len(imgs) < 1:
            log("没找到滑块图片，等一下")
            page.wait_for_timeout(1500)
            continue

        # 底图取面积最大的，拼图块取最小的
        imgs.sort(key=lambda x: x["w"] * x["h"], reverse=True)
        bg_bytes = _get(imgs[0]["src"], session)
        piece_bytes = _get(imgs[-1]["src"], session) if len(imgs) > 1 else None
        if not bg_bytes:
            continue
        bg = _decode(bg_bytes)

        gap = None
        if piece_bytes:
            piece = _decode(piece_bytes)
            gap = gap_by_template(bg, piece)
        if gap is None:
            gap = gap_by_columns(bg)
        if gap is None:
            log(f"第 {attempt} 次：没找到缺口")
            continue

        handle = page.evaluate(HANDLE_JS)
        if not handle:
            log(f"第 {attempt} 次：没找到滑动手柄")
            return False

        # 底图宽度和实际显示宽度的比例，换算成鼠标位移
        scale = handle and (bg.shape[1] / max(imgs[0]["w"], 1)) or 1.0
        distance = int(gap / scale)
        distance = max(10, min(distance, 380))
        log(f"第 {attempt} 次：缺口 x={gap}，换算位移 {distance}px")

        x0 = handle["x"] + handle["w"] / 2
        y0 = handle["y"] + handle["h"] / 2
        page.mouse.move(x0, y0)
        page.mouse.down()
        time.sleep(random.uniform(0.10, 0.18))
        cur = x0
        for dx, dt in human_track(distance):
            cur += dx
            page.mouse.move(cur, y0 + random.uniform(-1.0, 1.0))
            time.sleep(dt)
        page.mouse.move(x0 + distance, y0)
        time.sleep(random.uniform(0.12, 0.25))
        page.mouse.up()
        page.wait_for_timeout(2200)

        body = page.inner_text("body")
        if any(k in body for k in ("验证通过", "请完成安全验证".replace("请完成", "验证成功"), "发送成功")):
            log("滑块通过")
            return True
        if "验证失败" in body or "请重试" in body or "滑动" in body:
            log("这次没过，刷新再来")
            box = page.evaluate(REFRESH_JS)
            if box:
                page.mouse.click(box["x"], box["y"])
                page.wait_for_timeout(1500)
            continue

        # 面板消失也算过（有的载体会直接进下一步）
        if page.evaluate("() => document.querySelectorAll('[class*=geetest_panel_box]').length") == 0:
            log("面板消失，按通过处理")
            return True
    return False

# ---------------------------------------------------------------- 截图法：不依赖图片元素
def gap_from_panel(panel: np.ndarray) -> tuple[int, int] | None:
    """在面板截图里找拼图块和缺口，返回 (块x, 缺口x)。

    缺口用边缘模板匹配：拼图块的轮廓形状和缺口一致，比按颜色/暗度找稳。
    """
    hsv = cv2.cvtColor(panel, cv2.COLOR_BGR2HSV)
    mask = ((hsv[:, :, 1] > 110) & (hsv[:, :, 2] > 110)).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    if n <= 1:
        return None
    idx = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h, area = stats[idx]
    if area < 200:
        return None

    pm = mask[y:y + h, x:x + w].astype(np.uint8)
    e_panel = cv2.Canny(cv2.cvtColor(panel, cv2.COLOR_BGR2GRAY), 50, 150).astype(np.float32)
    e_piece = cv2.Canny(pm * 255, 50, 150).astype(np.float32)
    if e_piece.sum() < 10:
        e_piece = (pm * 255).astype(np.float32)

    res = cv2.matchTemplate(e_panel, e_piece, cv2.TM_CCORR_NORMED, mask=e_piece)
    res[:, max(0, x - w):x + w] = -1        # 屏蔽块自身位置
    _, score, _, loc = cv2.minMaxLoc(res)
    if score < 0.4:
        # 退回相关系数法
        res2 = cv2.matchTemplate(panel, panel[y:y + h, x:x + w], cv2.TM_CCOEFF_NORMED)
        res2[:, max(0, x - w):x + w] = -1
        _, score, _, loc = cv2.minMaxLoc(res2)
        if score < 0.3:
            return None
    return int(x), int(loc[0])


def solve_slider_screenshot(page, tries: int = 6, log=print,
                            panel_selectors: tuple[str, ...] = (
                                "[class*=geetest_window]", "[class*=geetest_widget]",
                                "[class*=geetest_canvas_img]")) -> bool:
    """截图 → 找块与缺口 → 拖。面板不在时返回 False。"""
    panel_el = None
    for sel in panel_selectors:
        if page.locator(sel).count():
            panel_el = page.locator(sel).first
            break
    if panel_el is None:
        log("没找到滑块面板")
        return False

    for attempt in range(1, tries + 1):
        # 网络慢的时候面板先显示「智能验证检测中」，要等图出来
        got = None
        panel = None
        for wait in range(30):
            png = panel_el.screenshot()
            panel = _decode(png)
            got = gap_from_panel(panel)
            if got:
                break
            body = page.inner_text("body")
            if "网络超时" in body or "点击此处重试" in body:
                r = page.evaluate(REFRESH_JS)
                if r:
                    page.mouse.click(r["x"], r["y"])
                    log("面板报了网络超时，点重试")
            page.wait_for_timeout(800)
        if not got:
            log(f"第 {attempt} 次：等了 24 秒仍没出图")
            continue
        px, gx = got
        dx_img = gx - px

        box = panel_el.bounding_box()
        scale = box["width"] / panel.shape[1] if box and panel.shape[1] else 1.0
        distance = int(dx_img * scale)
        log(f"第 {attempt} 次：块 x={px} 缺口 x={gx} 位移 {distance}px（缩放 {scale:.2f}）")

        handle = page.evaluate(HANDLE_JS)
        if not handle:
            log("没找到滑动手柄")
            return False

        x0 = handle["x"] + handle["w"] / 2
        y0 = handle["y"] + handle["h"] / 2
        page.mouse.move(x0, y0)
        page.mouse.down()
        time.sleep(random.uniform(0.10, 0.18))
        cur = x0
        for dx, dt in human_track(distance):
            cur += dx
            page.mouse.move(cur, y0 + random.uniform(-1.0, 1.0))
            time.sleep(dt)
        page.mouse.move(x0 + distance, y0)
        time.sleep(random.uniform(0.12, 0.25))
        page.mouse.up()
        page.wait_for_timeout(2500)

        body = page.inner_text("body")
        if "验证通过" in body or page.locator("[class*=geetest_panel_success]").count():
            log("滑块通过")
            return True
        if "验证失败" in body or "请重试" in body:
            log("没过，刷新重来")
            r = page.evaluate(REFRESH_JS)
            if r:
                page.mouse.click(r["x"], r["y"])
                page.wait_for_timeout(1800)
            continue
        # 面板消失也认为过了
        if page.locator("[class*=geetest_panel_box]").count() == 0:
            log("面板已消失，按通过处理")
            return True
    return False

# ---------------------------------------------------------------- canvas 版（实测通过率最好）
GET_CANVAS_JS = """
() => {
  const g = s => { const c = document.querySelector(s); return c ? c.toDataURL('image/png') : null; };
  return {bg: g('.geetest_canvas_bg'), slice: g('.geetest_canvas_slice')};
}
"""


def _data_url_to_img(url: str):
    import base64
    raw = base64.b64decode(url.split(",", 1)[1])
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)


def _piece_from_slice(sl):
    alpha = sl[:, :, 3] if sl.ndim == 3 and sl.shape[2] == 4 else None
    if alpha is None or alpha.max() == 0:
        alpha = (cv2.cvtColor(sl[:, :, :3], cv2.COLOR_BGR2GRAY) > 8).astype(np.uint8) * 255
    m = (alpha > 40).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1:
        return None
    idx = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h, area = stats[idx]
    if area < 200:
        return None
    mask = (lab == idx).astype(np.uint8) * 255
    return int(x), int(y), int(w), int(h), mask, sl[:, :, :3]


def _gap_from_canvases(bg, piece):
    x0, y0, w, h, mask, rgb = piece
    base = bg[:, :, :3]
    cands = []
    # 彩色
    r1 = cv2.matchTemplate(base, rgb[y0:y0+h, x0:x0+w], cv2.TM_CCORR_NORMED, mask=mask[y0:y0+h, x0:x0+w])
    r1[:, max(0, x0-w//2):x0+w+w//2] = -1
    _, s1, _, l1 = cv2.minMaxLoc(r1)
    if s1 >= 0.85:
        cands.append(("ccorr_color", int(l1[0]), float(s1)))
    # 灰度
    bgg = cv2.cvtColor(base, cv2.COLOR_BGR2GRAY)
    pcg = cv2.cvtColor(rgb[y0:y0+h, x0:x0+w], cv2.COLOR_BGR2GRAY)
    r2 = cv2.matchTemplate(bgg, pcg, cv2.TM_CCORR_NORMED, mask=mask[y0:y0+h, x0:x0+w])
    r2[:, max(0, x0-w//2):x0+w+w//2] = -1
    _, s2, _, l2 = cv2.minMaxLoc(r2)
    if s2 >= 0.85:
        cands.append(("ccorr_gray", int(l2[0]), float(s2)))
    # 边缘
    e_bg = cv2.Canny(bgg, 50, 150).astype(np.float32)
    e_p = cv2.Canny(mask[y0:y0+h, x0:x0+w], 50, 150).astype(np.float32)
    if e_p.sum() > 50:
        r3 = cv2.matchTemplate(e_bg, e_p, cv2.TM_CCORR_NORMED, mask=e_p)
        r3[:, max(0, x0-w//2):x0+w+w//2] = -1
        _, s3, _, l3 = cv2.minMaxLoc(r3)
        if s3 >= 0.5:
            cands.append(("edge", int(l3[0]), float(s3)))
    if not cands:
        return None, {}
    # 三种方法投票：取出现次数最多的 x
    xs = [c[1] for c in cands]
    best = max(set(xs), key=xs.count)
    return best, {c[0]: c[1] for c in cands}


def solve_slider_canvas(page, tries: int = 6, log=print) -> bool:
    """读 canvas 找缺口、拖过去；失败点刷新重来。"""
    for attempt in range(1, tries + 1):
        for _ in range(20):                      # 等画布出图（网络慢时会有）
            d = page.evaluate(GET_CANVAS_JS)
            if d.get("bg") and d.get("slice"):
                break
            page.wait_for_timeout(600)
        else:
            log("等不到 canvas")
            return False

        bg = _data_url_to_img(d["bg"])
        sl = _data_url_to_img(d["slice"])
        piece = _piece_from_slice(sl)
        if not piece:
            log("slice 里没有块")
            page.wait_for_timeout(1200)
            continue

        gap, detail = _gap_from_canvases(bg, piece)
        handle = page.evaluate(HANDLE_JS)
        if gap is None or not handle:
            log(f"算不出缺口 {detail}")
            page.wait_for_timeout(1200)
            continue

        box = page.locator("[class*=geetest_window]").first.bounding_box()
        scale = box["width"] / bg.shape[1] if box else 1.0
        distance = int((gap - piece[0]) * scale)
        log(f"第 {attempt} 次：位移 {distance}px 各方法 {detail}")

        x0 = handle["x"] + handle["w"] / 2
        y0 = handle["y"] + handle["h"] / 2
        page.mouse.move(x0, y0)
        page.mouse.down()
        time.sleep(random.uniform(0.10, 0.18))
        cur = x0
        for dx, dt in human_track(distance):
            cur += dx
            page.mouse.move(cur, y0 + random.uniform(-1.0, 1.0))
            time.sleep(dt)
        page.mouse.move(x0 + distance, y0)
        time.sleep(random.uniform(0.12, 0.25))
        page.mouse.up()
        page.wait_for_timeout(2200)

        body = page.inner_text("body")
        if "验证通过" in body or page.locator("[class*=geetest_panel_success]").count():
            log("滑块通过")
            return True
        r = page.evaluate(REFRESH_JS)
        if r:
            page.mouse.click(r["x"], r["y"])
            page.wait_for_timeout(1800)
    return False

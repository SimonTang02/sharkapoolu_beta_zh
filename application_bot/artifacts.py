"""用于浏览器辅助填写测试的私有追加式证据。"""

from __future__ import annotations

import base64
import json
import math
import re
import struct
from datetime import datetime
from pathlib import Path
from typing import Any


def _private_mode(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        pass


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")[:50] or "step"


def _png_dimensions(path: Path) -> tuple[int, int]:
    try:
        header = path.read_bytes()[:24]
        if header[:8] == b"\x89PNG\r\n\x1a\n" and len(header) >= 24:
            return struct.unpack(">II", header[16:24])
    except OSError:
        pass
    return (0, 0)


def _prepare_long_page(page: Any) -> dict[str, Any]:
    """截取整页之前，展开表单内部的滚动容器。"""

    try:
        return page.evaluate(
            """() => {
                const visible = el => {
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 1 && rect.height > 1 &&
                        style.display !== 'none' && style.visibility !== 'hidden';
                };
                const viewportWidth = Math.max(document.documentElement.clientWidth, innerWidth);
                const candidates = [...document.querySelectorAll('*')].map(el => {
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    const controls = el.querySelectorAll(
                        'input, textarea, select, [contenteditable="true"]'
                    ).length;
                    const overflow = `${style.overflow} ${style.overflowY}`;
                    const scrollable = el.scrollHeight > el.clientHeight + 24 &&
                        /(auto|scroll|hidden)/.test(overflow);
                    const wideEnough = rect.width >= viewportWidth * 0.42;
                    const score = controls * 1000000000 + el.scrollHeight * rect.width;
                    return {el, controls, scrollable, wideEnough, score};
                }).filter(item => item.scrollable && item.wideEnough && visible(item.el))
                  .sort((a, b) => b.score - a.score);

                const primary = candidates.length ? candidates[0].el : document.scrollingElement;
                const expanded = new Set();
                if (primary && primary !== document.scrollingElement) {
                    for (let el = primary; el && el !== document.documentElement; el = el.parentElement) {
                        expanded.add(el);
                    }
                }
                const states = [...expanded].map(el => ({
                    el,
                    style: el.getAttribute('style'),
                    scrollTop: el.scrollTop,
                    scrollLeft: el.scrollLeft,
                }));
                const rootStates = [document.documentElement, document.body].filter(Boolean).map(el => ({
                    el,
                    style: el.getAttribute('style'),
                    scrollTop: el.scrollTop,
                    scrollLeft: el.scrollLeft,
                }));
                const primaryScrollHeight = primary ? primary.scrollHeight : 0;
                const primaryClientHeight = primary ? primary.clientHeight : 0;
                for (const state of states) {
                    const height = Math.max(state.el.scrollHeight, state.el.clientHeight);
                    state.el.style.setProperty('height', `${height}px`, 'important');
                    state.el.style.setProperty('max-height', 'none', 'important');
                    state.el.style.setProperty('overflow', 'visible', 'important');
                    state.el.style.setProperty('overflow-y', 'visible', 'important');
                    state.el.style.setProperty('contain', 'none', 'important');
                }
                for (const state of rootStates) {
                    state.el.style.setProperty('height', 'auto', 'important');
                    state.el.style.setProperty('max-height', 'none', 'important');
                    state.el.style.setProperty('overflow-y', 'visible', 'important');
                }
                window.scrollTo(0, 0);
                window.__jobbotLongScreenshotState = {states, rootStates, primary};
                return {
                    supported: true,
                    scrollable_candidates: candidates.length,
                    expanded_elements: states.length,
                    primary_scroll_height: primaryScrollHeight,
                    primary_client_height: primaryClientHeight,
                };
            }"""
        )
    except Exception:
        return {
            "supported": False,
            "scrollable_candidates": 0,
            "expanded_elements": 0,
            "primary_scroll_height": 0,
            "primary_client_height": 0,
        }


def _restore_long_page(page: Any) -> None:
    try:
        page.evaluate(
            """() => {
                const saved = window.__jobbotLongScreenshotState;
                if (!saved) return;
                for (const state of [...saved.states, ...saved.rootStates]) {
                    if (state.style === null) state.el.removeAttribute('style');
                    else state.el.setAttribute('style', state.style);
                    state.el.scrollTop = state.scrollTop;
                    state.el.scrollLeft = state.scrollLeft;
                }
                delete window.__jobbotLongScreenshotState;
            }"""
        )
    except Exception:
        pass


def capture_long_page(
    page: Any,
    screenshot_path: Path,
    *,
    use_cdp: bool = True,
    full_page: bool = True,
    full_page_timeout_ms: int = 60_000,
) -> dict[str, Any]:
    """截取可供审阅的长页面，包括单页应用内部表单的滚动区域。

    对于非常高的页面，使用 CDP 分段截图，以避免超出 Chromium 的最大位图高度。
    不会静默地将证据缩减为当前视口。
    """

    prepared = (
        _prepare_long_page(page)
        if full_page
        else {
            "supported": False,
            "scrollable_candidates": 0,
            "expanded_elements": 0,
            "primary_scroll_height": 0,
            "primary_client_height": 0,
        }
    )
    paths: list[Path] = []
    mode = "full_page"
    layout_width = 0
    layout_height = 0
    try:
        try:
            page.wait_for_timeout(600)
        except Exception:
            pass
        if use_cdp and full_page:
            try:
                session = page.context.new_cdp_session(page)
                metrics = session.send("Page.getLayoutMetrics")
                content = metrics.get("cssContentSize") or metrics.get("contentSize") or {}
                layout_width = max(1, math.ceil(float(content.get("width") or 0)))
                layout_height = max(1, math.ceil(float(content.get("height") or 0)))
                if layout_width and layout_height:
                    segment_height = 20_000
                    segment_count = max(1, math.ceil(layout_height / segment_height))
                    for index in range(segment_count):
                        y = index * segment_height
                        height = min(segment_height, layout_height - y)
                        part_path = (
                            screenshot_path
                            if index == 0
                            else screenshot_path.with_name(
                                f"{screenshot_path.stem}_part{index + 1:02d}{screenshot_path.suffix}"
                            )
                        )
                        captured = session.send(
                            "Page.captureScreenshot",
                            {
                                "format": "png",
                                "captureBeyondViewport": True,
                                "clip": {
                                    "x": 0,
                                    "y": y,
                                    "width": layout_width,
                                    "height": height,
                                    "scale": 1,
                                },
                            },
                        )
                        part_path.write_bytes(base64.b64decode(captured["data"]))
                        paths.append(part_path)
                    mode = "expanded_cdp" if prepared["expanded_elements"] else "cdp_full_page"
                    if segment_count > 1:
                        mode += "_segmented"
            except Exception:
                paths = []

        if not paths:
            try:
                page.screenshot(
                    path=str(screenshot_path),
                    full_page=full_page,
                    timeout=full_page_timeout_ms if full_page else 15_000,
                    animations="disabled",
                    caret="hide",
                )
            except TypeError:
                # 用于精简测试替身和旧版 Playwright。
                page.screenshot(path=str(screenshot_path), full_page=full_page)
            except Exception:
                # Chromium 尝试截取某些 Oracle CX 页面时会无限期卡住，
                # 原因是页面全页表面已被转换。请保留
                # 有时限且可审阅的视口截图，避免丢失
                # 此次填写操作的全部证据。
                page.screenshot(
                    path=str(screenshot_path),
                    full_page=False,
                    timeout=15_000,
                    animations="disabled",
                    caret="hide",
                )
                mode = "viewport_fallback"
            paths = [screenshot_path]
            if mode != "viewport_fallback":
                if not full_page:
                    mode = "viewport"
                else:
                    mode = (
                        "expanded_playwright"
                        if prepared["expanded_elements"]
                        else "full_page"
                    )
    finally:
        _restore_long_page(page)

    for path in paths:
        _private_mode(path, 0o600)
    width, height = _png_dimensions(paths[0]) if paths else (0, 0)
    return {
        "mode": mode,
        "screenshot_paths": [str(path) for path in paths],
        "image_width": width,
        "image_height": height,
        "layout_width": layout_width,
        "layout_height": layout_height,
        **prepared,
    }


def save_fill_test_artifact(
    page: Any,
    output_dir: Path,
    *,
    adapter: str,
    stage: str,
    status: str,
    metadata: dict[str, Any] | None = None,
    use_cdp: bool = True,
    full_page: bool = True,
    full_page_timeout_ms: int = 60_000,
) -> dict[str, Any]:
    """保存带时间戳的截图和不含字段值的 JSONL 审计记录。"""
    artifact_dir = output_dir / "fill_tests"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    _private_mode(output_dir, 0o700)
    _private_mode(artifact_dir, 0o700)
    now = datetime.now().astimezone()
    stamp = now.strftime("%Y%m%d_%H%M%S_%f")
    screenshot_path = artifact_dir / (
        f"{stamp}_{_slug(adapter)}_{_slug(stage)}_{_slug(status)}.png"
    )
    capture = capture_long_page(
        page,
        screenshot_path,
        use_cdp=use_cdp,
        full_page=full_page,
        full_page_timeout_ms=full_page_timeout_ms,
    )
    screenshot_paths = capture["screenshot_paths"]
    if not screenshot_paths:
        raise RuntimeError("长页面截图未生成任何文件")
    screenshot_path = Path(screenshot_paths[0])
    record = {
        "captured_at": now.isoformat(timespec="seconds"),
        "adapter": adapter,
        "stage": stage,
        "status": status,
        "page_title": page.title(),
        "url": page.url,
        "screenshot_path": str(screenshot_path),
        "screenshot_paths": screenshot_paths,
        "capture": capture,
        "metadata": metadata or {},
        "submit_clicked": False,
    }
    index_path = artifact_dir / "index.jsonl"
    with index_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    _private_mode(index_path, 0o600)
    return record

"""可选择在完整扫描过程中复用一个明确归本进程所有的 CDP 目标。"""

from __future__ import annotations

import os
from typing import Any


def target_id(page: Any) -> str:
    session = page.context.new_cdp_session(page)
    try:
        return str(session.send("Target.getTargetInfo")["targetInfo"]["targetId"])
    finally:
        session.detach()


def new_scan_page(context: Any) -> Any:
    owned_target = os.environ.get("JOBBOT_SCAN_TARGET_ID", "").strip()
    if not owned_target:
        return context.new_page()
    for page in context.pages:
        if page.is_closed():
            continue
        try:
            current_target = target_id(page)
        except Exception:
            continue
        if current_target == owned_target:
            return page
    raise RuntimeError("专用扫描标签页不可用；拒绝改用其他标签页")


def close_scan_page(page: Any) -> None:
    owned_target = os.environ.get("JOBBOT_SCAN_TARGET_ID", "").strip()
    if owned_target:
        if target_id(page) != owned_target:
            raise RuntimeError("拒绝关闭专用扫描目标之外的标签页")
        # 在不同来源的扫描之间保持窗口和已认证上下文处于活动状态。
        return
    page.close()

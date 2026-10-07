#!/usr/bin/env python3
"""对专用 Chrome 中已打开的申请门户进行只读审计。

本模块不会点击、填写、上传、接受政策或提交。它生成简明的准备情况报告，供判断哪些门户适配器可以安全地进入人工审阅草稿阶段。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_PACKAGES = PROJECT_ROOT / ".python_packages"
if LOCAL_PACKAGES.is_dir() and str(LOCAL_PACKAGES) not in sys.path:
    sys.path.insert(0, str(LOCAL_PACKAGES))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from job_bot.application_bot import resolve_browser_connection  # noqa: E402
from job_bot.bot import load_config, load_env_file  # noqa: E402
from job_bot.browser_connection import check_cdp_health  # noqa: E402
from application_bot.portal_registry import resolve_adapter_by_url  # noqa: E402
from private_paths import APPLICATION_OUTPUT, CREDENTIALS_FILE  # noqa: E402


DEFAULT_CONFIG = PROJECT_ROOT / "job_bot" / "config.china_hk_ic_foreign.json"
DEFAULT_ENV = CREDENTIALS_FILE
DEFAULT_OUT = APPLICATION_OUTPUT


@dataclass(frozen=True)
class PortalAudit:
    platform: str
    title: str
    url: str
    state: str
    reason: str
    visible_inputs: int
    visible_file_inputs: int
    simplify_present: bool
    safe_next_action: str


def platform_for_url(url: str, config: dict) -> str | None:
    adapter = resolve_adapter_by_url(config, url)
    return adapter.id if adapter else None


def classify_portal(
    *,
    platform: str,
    url: str,
    title: str,
    text: str,
    visible_inputs: int,
    visible_file_inputs: int,
) -> tuple[str, str, str]:
    """进行分类时，不要将最终操作误判为草稿操作。"""
    normalized = " ".join(text.lower().split())
    title_l = title.lower()
    url_l = url.lower()

    if "hcaptcha" in normalized or "protected by hcaptcha" in normalized:
        return (
            "captcha_or_consent_required",
            "门户要求先由人工确认同意事项和/或完成 hCaptcha。",
            "人工审阅：如愿意，请接受所列政策并完成 hCaptcha。",
        )
    if platform == "mediatek" and (
        "提交申请" in text or "your application will be submitted" in normalized
    ):
        return (
            "final_submit_only",
            "下一个控件会最终提交申请，而非保存草稿。",
            "不要自动继续；须明确决定是否最终提交。",
        )
    if (
        "sign in" in title_l
        or title_l.startswith("login")
        or "create account" in title_l
        or "/login" in url_l
        or "/authorize" in url_l
        or "returning user login" in normalized
        or "登录" in normalized[:1200]
        or "登錄" in normalized[:1200]
    ):
        return (
            "authentication_required",
            "当前申请会话未处于已登录且可编辑的表单页面。",
            "请在专用 Chrome 中手动登录；遇到 MFA/CAPTCHA 时停止。",
        )
    if platform == "mediatek" and (
        "完善您的简历" in text
        or "请填写至少1段教育背景" in text
        or "请输入以下信息" in text
    ):
        return (
            "profile_incomplete",
            "账户档案不完整，且包含敏感必填字段。",
            "仅填写已明确授权的事实字段和敏感档案字段。",
        )
    if any(word in title_l for word in ("principal", "senior", "staff", "director")):
        return (
            "role_mismatch",
            "当前打开的岗位属于高级职位，不在已配置的早期职业队列中。",
            "不要填写；请改选已评分的实习或应届毕业岗位。",
        )
    if platform == "simplify" and "/search" in url_l:
        return (
            "discovery_helper",
            "当前打开的是 Simplify 搜索/自动填写助手，而非雇主申请表。",
            "仅在打开受支持的雇主申请表后使用该扩展。",
        )
    if platform in {"analog_devices", "huawei"} and any(
        marker in url_l for marker in ("/careers.html", "job-list")
    ):
        return (
            "listing_or_redirect",
            "这是招聘搜索/落地页，不是申请表。",
            "尝试自动填写前，请先选择匹配度高的早期职业岗位。",
        )
    if visible_inputs or visible_file_inputs:
        return (
            "form_detected",
            "页面中有可编辑表单，可交由门户适配器处理。",
            "运行禁止提交的预览，填写已知字段，并在 Review 阶段停止。",
        )
    if any(word in normalized for word in ("apply", "申请", "應徵", "职位", "職位")):
        return (
            "listing_or_redirect",
            "当前是岗位/列表页面，但没有可见的可编辑申请表。",
            "打开一次申请入口，然后重新运行审计。",
        )
    return (
        "unsupported_or_landing",
        "未检测到已登录且可编辑的申请表。",
        "保留此来源用于岗位发现；不要盲目尝试自动填写。",
    )


def audit_page(page, platform: str) -> PortalAudit:
    try:
        title = page.title().strip()
    except Exception:
        title = ""
    text_parts: list[str] = []
    visible_inputs = 0
    visible_file_inputs = 0
    for frame in page.frames:
        try:
            text_parts.append(frame.locator("body").inner_text(timeout=2_000)[:40_000])
        except Exception:
            pass
        try:
            inputs = frame.locator(
                "input:not([type=hidden]):not([type=file]), textarea, select"
            )
            visible_inputs += sum(
                inputs.nth(i).is_visible() for i in range(inputs.count())
            )
        except Exception:
            pass
        try:
            files = frame.locator("input[type=file]")
            visible_file_inputs += sum(
                files.nth(i).is_visible() for i in range(files.count())
            )
        except Exception:
            pass
    text = "\n".join(text_parts)[:80_000]
    try:
        simplify_present = any(
            frame.url.startswith("chrome-extension://") for frame in page.frames
        ) or page.locator('[class*="simplify" i], [id*="simplify" i]').count() > 0
    except Exception:
        simplify_present = False
    state, reason, next_action = classify_portal(
        platform=platform,
        url=page.url,
        title=title,
        text=text,
        visible_inputs=visible_inputs,
        visible_file_inputs=visible_file_inputs,
    )
    return PortalAudit(
        platform=platform,
        title=title,
        url=page.url,
        state=state,
        reason=reason,
        visible_inputs=visible_inputs,
        visible_file_inputs=visible_file_inputs,
        simplify_present=simplify_present,
        safe_next_action=next_action,
    )


def render_markdown(records: list[PortalAudit], generated_at: str) -> str:
    lines = [
        "# 申请平台审计",
        "",
        f"生成时间：{generated_at}",
        "",
        "只读审计。未填写任何表单，也未提交任何申请。",
        "",
        "| 平台 | 状态 | 输入框 | Simplify | 安全的下一步操作 |",
        "|---|---|---:|:---:|---|",
    ]
    for item in records:
        lines.append(
            f"| {item.platform} | {item.state} | "
            f"{item.visible_inputs}/{item.visible_file_inputs} 个文件输入框 | "
            f"{'是' if item.simplify_present else '否'} | {item.safe_next_action} |"
        )
    lines.append("")
    lines.append("## 详情")
    lines.append("")
    for item in records:
        lines.extend(
            [
                f"### {item.platform}",
                "",
                f"- 页面：{item.title or '(无标题)'}",
                f"- 网址：{item.url}",
                f"- 发现：{item.reason}",
                "",
            ]
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--env-file", default=str(DEFAULT_ENV))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(argv)

    load_env_file(Path(args.env_file))
    config = load_config(Path(args.config))
    mode, endpoint = resolve_browser_connection(config)
    if mode != "windows_cdp":
        raise SystemExit("platform-audit 需要专用 Windows CDP 浏览器")
    health = check_cdp_health(endpoint)

    from playwright.sync_api import sync_playwright

    records: list[PortalAudit] = []
    seen: set[str] = set()
    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.connect_over_cdp(health.connect_url)
        for context in browser.contexts:
            for page in reversed(context.pages):
                platform = platform_for_url(page.url, config)
                if not platform or platform in seen:
                    continue
                seen.add(platform)
                records.append(audit_page(page, platform))
                if len(records) >= args.limit:
                    break
            if len(records) >= args.limit:
                break
    finally:
        playwright.stop()

    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"platform_audit_{stamp}.json"
    md_path = out_dir / f"platform_audit_{stamp}.md"
    json_path.write_text(
        json.dumps(
            {"generated_at": generated_at, "browser": health.browser, "portals": [asdict(r) for r in records]},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(records, generated_at) + "\n", encoding="utf-8")
    print(md_path)
    print(json_path)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""基于门户注册表执行并发只读会话审计。"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
LOCAL_PACKAGES = ROOT / ".python_packages"
if LOCAL_PACKAGES.is_dir() and str(LOCAL_PACKAGES) not in sys.path:
    sys.path.insert(0, str(LOCAL_PACKAGES))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from application_bot.portal_registry import adapters_from_config, resolve_adapter  # noqa: E402
from job_bot.application_bot import resolve_browser_connection  # noqa: E402
from job_bot.bot import connect_db, load_config, load_env_file  # noqa: E402
from job_bot.browser_connection import check_cdp_health  # noqa: E402
from private_paths import APPLICATION_OUTPUT, CREDENTIALS_FILE  # noqa: E402


DEFAULT_CONFIG = ROOT / "job_bot" / "config.china_hk_ic_foreign.json"
SOURCE_PLATFORM = {
    "jobsdb_hk": "jobsdb_hk_cdp",
    "oracle_ce": "oracle_candidate_experience",
    "shixiseng": "shixiseng_cdp",
    "moka_cdp": "moka_cdp",
}


@dataclass(frozen=True)
class SessionProbe:
    company: str
    adapter: str
    scope: str
    url: str | None
    unavailable_reason: str
    sources: tuple[str, ...]


@dataclass(frozen=True)
class SessionResult:
    company: str
    adapter: str
    scope: str
    probe_url: str
    state: str
    final_host: str
    http_status: int | None
    elapsed_ms: int
    reason: str
    sources: tuple[str, ...]


def _latest_applications(conn) -> dict[str, dict[str, str]]:
    rows = conn.execute(
        """
        SELECT jobs.company, jobs.url, jobs.platform, jobs.external_id,
               applications.draft_url
        FROM applications JOIN jobs ON jobs.id=applications.job_id
        WHERE applications.status <> 'cancelled'
        ORDER BY applications.id DESC
        """
    ).fetchall()
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        result.setdefault(
            str(row["company"] or "").casefold(),
            {key: str(row[key] or "") for key in ("url", "platform", "external_id", "draft_url")},
        )
    return result


def build_probes(config: dict[str, Any], conn) -> list[SessionProbe]:
    applications = _latest_applications(conn)
    raw_probes: dict[tuple[str, str], dict[str, Any]] = {}
    for source in config.get("sources", []):
        if source.get("enabled", True) is False:
            continue
        company = str(source.get("company") or source.get("name") or "Unknown")
        source_type = str(source.get("type", "")).casefold()
        platform = SOURCE_PLATFORM.get(source_type, source_type)
        source_url = str(
            source.get("url")
            or source.get("host")
            or source.get("api_url")
            or source.get("public_url")
            or source.get("search_url")
            or source.get("public_host")
            or source.get("official_reference")
            or ""
        )
        adapter = resolve_adapter(
            config, company=company, platform=platform, url=source_url
        )
        if not adapter:
            continue
        probe_config = adapter.session_probe
        kind = str(probe_config.get("kind", "unsupported"))
        app = applications.get(company.casefold(), {})
        url: str | None = None
        reason = ""
        if kind == "source_template":
            values = {
                "host": str(source.get("host", "")).rstrip("/"),
                "site": str(source.get("site", "")).strip("/"),
                "company": company,
            }
            try:
                url = str(probe_config["template"]).format(**values)
                scope = str(probe_config.get("scope", "{company}")).format(**values)
            except (KeyError, ValueError):
                scope = company
                reason = "来源未提供探测模板所需字段"
        elif kind == "fixed_url":
            url = str(probe_config.get("url") or "") or None
            scope = str(probe_config.get("scope") or adapter.id)
        elif kind == "application_template":
            scope = company
            if app.get("external_id"):
                url = str(probe_config.get("template", "")).format(**app)
            else:
                reason = "没有带外部 ID 的代表性申请"
        elif kind == "application_url":
            scope = company
            url = app.get("draft_url") or app.get("url") or None
            if not url:
                reason = "此门户没有可用的代表性申请"
        else:
            scope = company
            reason = str(
                probe_config.get("reason")
                or "未配置安全的只读会话端点"
            )
        key = (adapter.id, scope.casefold())
        item = raw_probes.setdefault(
            key,
            {
                "company": company,
                "adapter": adapter.id,
                "scope": scope,
                "url": url,
                "reason": reason,
                "sources": [],
            },
        )
        item["sources"].append(str(source.get("name", company)))
        if not item["url"] and url:
            item["url"] = url
            item["reason"] = ""

    configured_adapter_ids = {adapter.id for adapter in adapters_from_config(config)}
    if not configured_adapter_ids:
        return []
    return sorted(
        (
            SessionProbe(
                company=str(item["company"]),
                adapter=str(item["adapter"]),
                scope=str(item["scope"]),
                url=str(item["url"]) if item["url"] else None,
                unavailable_reason=str(item["reason"]),
                sources=tuple(item["sources"]),
            )
            for item in raw_probes.values()
        ),
        key=lambda item: (item.adapter, item.company.casefold(), item.scope.casefold()),
    )


def classify_session(
    *,
    adapter: str,
    url: str,
    title: str,
    body: str,
    http_status: int | None,
    password_visible: bool,
    visible_controls: int,
) -> tuple[str, str]:
    normalized = " ".join(f"{title} {body}".casefold().split())
    url_l = url.casefold()
    if http_status is not None and http_status >= 400:
        return "access_error", f"探测返回 HTTP {http_status}"
    if adapter == "handshake":
        from urllib.parse import urlsplit
        host = urlsplit(url).hostname or ""
        if host.endswith(".duosecurity.com"):
            return "challenge_required", "PennKey Duo 验证需要用户手动完成"
        if "pennkey" in host or "continue with email" in normalized:
            return "authentication_required", "需要登录 PennKey/Handshake"
    if any(marker in normalized for marker in ("captcha", "hcaptcha", "verification code", "security code", "验证码", "驗證碼")):
        return "challenge_required", "页面显示 CAPTCHA 或账户验证挑战"
    login_url = any(marker in url_l for marker in ("/login", "/signin", "/sign-in"))
    login_text = any(
        marker in normalized[:6000]
        for marker in ("sign in", "log in", "returning candidate", "create account", "登录", "登錄")
    )
    if password_visible or login_url or login_text:
        return "authentication_required", "页面显示登录边界"
    if adapter == "handshake" and host in {"upenn.joinhandshake.com", "app.joinhandshake.com"}:
        if "/job-search" in url_l and "resume optimizer" in normalized and "saved" in normalized:
            return "authenticated", "已显示 Handshake 学生职位搜索导航"
    authenticated_text = any(
        marker in normalized
        for marker in ("candidate home", "my applications", "job alerts", "saved jobs", "我的申请", "个人中心", "我的简历")
    )
    if "/userhome" in url_l or authenticated_text:
        return "authenticated", "已显示登录后的候选人/档案页面"
    if adapter in {"eightfold", "infineon_eightfold", "ti_oracle", "amd_icims", "generic_icims"} and visible_controls:
        return "application_accessible", "无需越过登录边界即可访问可编辑的申请页面"
    return "unknown", "页面已加载，但没有可靠的登录或登出状态信号"


async def audit_probes(
    probes: list[SessionProbe], endpoint: str, *, concurrency: int, timeout_ms: int
) -> tuple[str, list[SessionResult]]:
    from playwright.async_api import async_playwright

    health = check_cdp_health(endpoint)
    semaphore = asyncio.Semaphore(max(1, concurrency))
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(
            health.connect_url, timeout=30_000
        )
        context = browser.contexts[0]

        async def audit_one(probe: SessionProbe) -> SessionResult:
            if not probe.url:
                return SessionResult(
                    probe.company, probe.adapter, probe.scope, "",
                    "not_safely_checkable", "", None, 0,
                    probe.unavailable_reason, probe.sources,
                )
            async with semaphore:
                page = await context.new_page()
                loop = asyncio.get_running_loop()
                started = loop.time()
                status: int | None = None
                final_host = ""
                try:
                    await page.evaluate("() => { window.name = 'jobbot-session-probe'; }")
                    response = await page.goto(
                        probe.url, wait_until="domcontentloaded", timeout=timeout_ms
                    )
                    status = response.status if response else None
                    await page.wait_for_timeout(500)
                    title = await page.title()
                    try:
                        body = (await page.locator("body").inner_text(timeout=2_000))[:40_000]
                    except Exception:
                        body = ""
                    passwords = page.locator("input[type=password]")
                    password_visible = False
                    for index in range(await passwords.count()):
                        if await passwords.nth(index).is_visible():
                            password_visible = True
                            break
                    controls = page.locator("input:not([type=hidden]):not([type=password]), textarea, select")
                    visible_controls = 0
                    for index in range(await controls.count()):
                        if await controls.nth(index).is_visible():
                            visible_controls += 1
                    state, reason = classify_session(
                        adapter=probe.adapter,
                        url=page.url,
                        title=title,
                        body=body,
                        http_status=status,
                        password_visible=password_visible,
                        visible_controls=visible_controls,
                    )
                    final_host = urlsplit(page.url).hostname or ""
                except Exception as exc:
                    state = "probe_error"
                    reason = f"{type(exc).__name__}: {' '.join(str(exc).split())[-300:]}"
                    final_host = urlsplit(page.url).hostname or ""
                finally:
                    elapsed_ms = int((loop.time() - started) * 1000)
                    await page.close()
                return SessionResult(
                    probe.company, probe.adapter, probe.scope, probe.url, state, final_host,
                    status, elapsed_ms, reason, probe.sources,
                )

        results = await asyncio.gather(*(audit_one(probe) for probe in probes))
    return health.browser, list(results)


def render_markdown(results: list[SessionResult], generated_at: str, browser: str) -> str:
    counts: dict[str, int] = {}
    for result in results:
        counts[result.state] = counts.get(result.state, 0) + 1
    lines = [
        "# 申请平台登录状态审计",
        "",
        f"生成时间：{generated_at}",
        f"浏览器：{browser}",
        "",
        "只读检查：未填写字段、未上传文件、未点击申请或提交按钮；临时探针标签页均已关闭。",
        "",
        "## 汇总",
        "",
        *(f"- {state}: {count}" for state, count in sorted(counts.items())),
        "",
        "## 平台",
        "",
        "| 公司/租户 | 适配器 | 状态 | HTTP | 耗时 | 说明 |",
        "|---|---|---|---:|---:|---|",
    ]
    for item in results:
        lines.append(
            f"| {item.company} | {item.adapter} | {item.state} | "
            f"{item.http_status or ''} | {item.elapsed_ms} ms | {item.reason} |"
        )
    lines.extend(
        (
            "",
            "`authenticated` 才表示确认已登录；`application_accessible` 只表示申请页面可访问。"
            " `not_safely_checkable` 表示不启动申请就没有可靠的登录探针。",
            "",
        )
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--env-file", type=Path, default=CREDENTIALS_FILE)
    parser.add_argument("--out-dir", type=Path, default=APPLICATION_OUTPUT)
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--timeout-ms", type=int)
    args = parser.parse_args()
    if args.env_file.is_file():
        load_env_file(args.env_file)
    config = load_config(args.config)
    mode, endpoint = resolve_browser_connection(config)
    if mode != "windows_cdp":
        raise SystemExit("session-audit 需要 application_browser.mode=windows_cdp")
    audit_config = config.get("portals", {}).get("session_audit", {})
    conn = connect_db(config)
    probes = build_probes(config, conn)
    conn.close()
    browser, results = asyncio.run(
        audit_probes(
            probes,
            endpoint,
            concurrency=args.concurrency or int(audit_config.get("concurrency", 4)),
            timeout_ms=args.timeout_ms or int(audit_config.get("timeout_ms", 20_000)),
        )
    )
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    base = args.out_dir / f"session_audit_{datetime.now():%Y%m%d_%H%M%S}"
    md_path = base.with_suffix(".md")
    json_path = base.with_suffix(".json")
    md_path.write_text(render_markdown(results, generated_at, browser), encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {"generated_at": generated_at, "browser": browser, "results": [asdict(result) for result in results]},
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    print(md_path)
    print(json_path)
    print(" ".join(f"{state}={sum(item.state == state for item in results)}" for state in sorted({item.state for item in results})))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""仅打开最近一次基于配置的审计标记出的会话页面。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_PACKAGES = ROOT / ".python_packages"
if LOCAL_PACKAGES.is_dir() and str(LOCAL_PACKAGES) not in sys.path:
    sys.path.insert(0, str(LOCAL_PACKAGES))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from job_bot.application_bot import resolve_browser_connection  # noqa: E402
from job_bot.applications.nvidia_workday import _playwright_api  # noqa: E402
from job_bot.bot import load_config, load_env_file  # noqa: E402
from private_paths import APPLICATION_OUTPUT, CREDENTIALS_FILE  # noqa: E402


DEFAULT_CONFIG = ROOT / "job_bot/config.china_hk_ic_foreign.json"
ACTIONABLE_STATES = {"authentication_required", "challenge_required"}


def latest_audit() -> Path:
    candidates = sorted(APPLICATION_OUTPUT.glob("session_audit_*.json"))
    if not candidates:
        raise SystemExit(
            "尚无会话审计结果；请先运行 application_bot/session_audit.py"
        )
    return candidates[-1]


def safe_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")[:80]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--env-file", type=Path, default=CREDENTIALS_FILE)
    parser.add_argument("--audit", type=Path)
    args = parser.parse_args()
    if args.env_file.is_file():
        load_env_file(args.env_file)
    config = load_config(args.config)
    mode, cdp_url = resolve_browser_connection(config)
    if mode != "windows_cdp":
        raise SystemExit("登录队列需要 application_browser.mode=windows_cdp")
    audit_path = args.audit or latest_audit()
    payload = json.loads(audit_path.read_text(encoding="utf-8"))
    targets = [
        item
        for item in payload.get("results", [])
        if item.get("state") in ACTIONABLE_STATES and item.get("probe_url")
    ]

    opened = []
    sync_playwright = _playwright_api()
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(cdp_url, timeout=30_000)
        context = browser.contexts[0]
        existing_labels = set()
        for page in context.pages:
            try:
                existing_labels.add(str(page.evaluate("window.name") or ""))
            except Exception:
                pass
        for item in targets:
            scope = str(item.get("scope") or item["adapter"])
            label = f"jobbot-login-{safe_label(scope)}"
            if label in existing_labels:
                opened.append(
                    {"scope": scope, "state": "already_open", "label": label}
                )
                continue
            page = context.new_page()
            page.evaluate("label => { window.name = label; }", label)
            error = ""
            try:
                page.goto(
                    item["probe_url"],
                    wait_until="domcontentloaded",
                    timeout=45_000,
                )
            except Exception as exc:
                error = f"{type(exc).__name__}: {' '.join(str(exc).split())[-300:]}"
            opened.append(
                {
                    "scope": scope,
                    "company": item.get("company"),
                    "adapter": item.get("adapter"),
                    "state": "opened" if not error else "open_error",
                    "label": label,
                    "error": error,
                }
            )

    APPLICATION_OUTPUT.mkdir(parents=True, exist_ok=True)
    output = APPLICATION_OUTPUT / (
        f"login_tab_queue_{datetime.now():%Y%m%d_%H%M%S}.json"
    )
    output.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().astimezone().isoformat(
                    timespec="seconds"
                ),
                "source_audit": str(audit_path),
                "opened": opened,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(output)
    print(f"actionable={len(targets)} opened_or_reused={len(opened)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

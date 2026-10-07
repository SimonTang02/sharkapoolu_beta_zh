#!/usr/bin/env python3
"""在限时采集证据后，仅关闭明确指定的申请标签页。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_PACKAGES = ROOT / ".python_packages"
if LOCAL_PACKAGES.is_dir() and str(LOCAL_PACKAGES) not in sys.path:
    sys.path.insert(0, str(LOCAL_PACKAGES))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from application_bot.artifacts import save_fill_test_artifact  # noqa: E402
from application_bot.tab_registry import mark_application_tab_closed  # noqa: E402
from job_bot.application_bot import resolve_browser_connection  # noqa: E402
from job_bot.applications.nvidia_workday import _playwright_api  # noqa: E402
from job_bot.bot import connect_db, load_config, load_env_file  # noqa: E402
from private_paths import CREDENTIALS_FILE, JOBBOT_OUTPUT  # noqa: E402


DEFAULT_CONFIG = ROOT / "job_bot/config.china_hk_ic_foreign.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("application_ids", nargs="*", type=int)
    parser.add_argument(
        "--login-scope",
        action="append",
        default=[],
        help="采集证据后关闭一个确切的 jobbot-login-SCOPE 标签页。",
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--env", default=str(CREDENTIALS_FILE))
    args = parser.parse_args()
    if not args.application_ids and not args.login_scope:
        raise SystemExit("请提供申请 ID 或 --login-scope")

    load_env_file(Path(args.env))
    config = load_config(Path(args.config))
    mode, cdp_url = resolve_browser_connection(config)
    if mode != "windows_cdp":
        raise SystemExit("精确清理标签页需要 windows_cdp 模式")
    conn = connect_db(config)
    requested = set(args.application_ids)
    known = set()
    if requested:
        known = {
            int(row["id"])
            for row in conn.execute(
                "SELECT id FROM applications WHERE id IN (%s)"
                % ",".join("?" for _ in requested),
                tuple(sorted(requested)),
            )
        }
    if known != requested:
        raise SystemExit(f"未知的申请 ID： {sorted(requested - known)}")

    sync_playwright = _playwright_api()
    closed: list[int] = []
    skipped: list[int] = []
    closed_scopes: list[str] = []
    skipped_scopes: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(cdp_url, timeout=30_000)
        pages = [page for context in browser.contexts for page in context.pages]
        for application_id in sorted(requested):
            label = f"jobbot-application-{application_id}"
            matches = []
            for page in pages:
                if page.is_closed():
                    continue
                try:
                    if page.evaluate("window.name") == label:
                        matches.append(page)
                except Exception:
                    continue
            if not matches:
                skipped.append(application_id)
                continue
            page = matches[-1]
            try:
                save_fill_test_artifact(
                    page,
                    JOBBOT_OUTPUT / "applications" / str(application_id),
                    adapter="exact_tab_cleanup",
                    stage="before_close",
                    status="user_requested_cleanup",
                    metadata={"application_id": application_id},
                    use_cdp=False,
                    full_page=False,
                )
            except Exception as exc:
                print(f"跳过 {application_id}：采集证据失败：{exc}")
                skipped.append(application_id)
                continue
            page.close()
            mark_application_tab_closed(conn, application_id)
            closed.append(application_id)
        for scope in args.login_scope:
            label = f"jobbot-login-{scope}"
            matches = []
            for page in pages:
                if page.is_closed():
                    continue
                try:
                    if page.evaluate("window.name") == label:
                        matches.append(page)
                except Exception:
                    continue
            if len(matches) != 1:
                skipped_scopes.append(scope)
                continue
            page = matches[0]
            try:
                save_fill_test_artifact(
                    page,
                    JOBBOT_OUTPUT / "login_queue_cleanup",
                    adapter="exact_tab_cleanup",
                    stage="before_close",
                    status="user_requested_cleanup",
                    metadata={"login_scope": scope},
                    use_cdp=False,
                    full_page=False,
                )
            except Exception as exc:
                print(f"跳过 {scope}：采集证据失败：{exc}")
                skipped_scopes.append(scope)
                continue
            page.close()
            closed_scopes.append(scope)
    print(
        f"closed={closed} skipped={skipped} "
        f"closed_scopes={closed_scopes} skipped_scopes={skipped_scopes}"
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""在现有已认证 Chrome 的独立时间窗口中运行今日流程。"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from job_bot.bot import DEFAULT_CONFIG, db_path, load_config, load_env_file
from job_bot.shared_database import connect as connect_database
from job_bot.applications.nvidia_workday import _playwright_api
from job_bot.browser_connection import check_cdp_health
from job_bot.daily_pipeline import cdp_endpoint
from job_bot.scan_browser import new_scan_page
from private_paths import CREDENTIALS_FILE, JOBBOT_OUTPUT


def main() -> int:
    load_env_file(CREDENTIALS_FILE)
    config = load_config(DEFAULT_CONFIG)
    health = check_cdp_health(cdp_endpoint(config))
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = JOBBOT_OUTPUT / ("isolated_scan_" + stamp)
    run_dir.mkdir(parents=True, mode=0o700)
    with connect_database(db_path(config)) as source, sqlite3.connect(run_dir / "before.sqlite3") as backup:
        source.backup(backup)
    manifest = {"started_at": dt.datetime.now(dt.timezone.utc).isoformat(), "browser": health.browser}
    with _playwright_api()() as playwright:
        browser = playwright.chromium.connect_over_cdp(health.connect_url, timeout=30000)
        session = browser.new_browser_cdp_session()
        manifest["existing_targets"] = [t["targetId"] for t in session.send("Target.getTargets")["targetInfos"] if t["type"] == "page"]
        owned = os.environ.get("JOBBOT_SCAN_TARGET_ID", "").strip()
        if not owned:
            with browser.contexts[0].expect_page(timeout=15000):
                result = session.send("Target.createTarget", {
                    "url": "about:blank", "newWindow": True, "background": True,
                    "focus": False, "width": 1280, "height": 900,
                })
                owned = result["targetId"]
                manifest["scan_target"] = owned
                (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        os.environ["JOBBOT_SCAN_TARGET_ID"] = owned
        manifest["scan_target"] = owned
        manifest["scan_window"] = session.send("Browser.getWindowForTarget", {"targetId": owned})["windowId"]
        page = new_scan_page(browser.contexts[0])
        page.evaluate("document.title = 'Job Bot — 隔离周扫描'")
        manifest["same_browser_context"] = True
        (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        session.detach()
        browser.close()  # 断开此 CDP 客户端；Chrome 及其页面继续运行。
    print("已创建隔离扫描窗口；未导出 Cookie。", flush=True)
    print("运行目录：", run_dir, flush=True)
    with (run_dir / "pipeline.log").open("w") as log:
        result = subprocess.run([sys.executable, "job_bot/daily_pipeline.py", "--no-browser-start"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode == 0:
            result = subprocess.run([sys.executable, "job_bot/bot.py", "digest", "--config", str(DEFAULT_CONFIG), "--env-file", str(CREDENTIALS_FILE)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    manifest["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    manifest["returncode"] = result.returncode
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("流程退出码：", result.returncode, flush=True)
    print("日志：", run_dir / "pipeline.log", flush=True)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

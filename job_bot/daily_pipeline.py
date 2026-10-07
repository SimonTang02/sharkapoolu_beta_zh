#!/usr/bin/env python3
"""通过单条命令完成每日采集、评分、报告和跟进记录。"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from job_bot.bot import (  # noqa: E402
    db_path,
    load_config,
    load_env_file,
    rescore_jobs,
    scan,
    source_uses_cdp,
    transient_source_error,
)
from job_bot.browser_connection import CdpHealthError, check_cdp_health  # noqa: E402
from private_paths import CREDENTIALS_FILE, JOBBOT_OUTPUT  # noqa: E402
from job_bot.shared_database import connect as connect_database  # noqa: E402


DEFAULT_CONFIG = ROOT / "job_bot" / "config.china_hk_ic_foreign.json"
DEFAULT_ENV = CREDENTIALS_FILE
DEFAULT_STATE = JOBBOT_OUTPUT / "daily_pipeline_state.json"


def local_now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def enabled_cdp_sources(config: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        source
        for source in config.get("sources", [])
        if source.get("enabled", True) is not False and source_uses_cdp(source)
    ]


def cdp_endpoint(config: dict[str, Any]) -> str:
    browser = config.get("application_browser", {})
    mode = str(browser.get("mode", "local_persistent"))
    mode_config = browser.get(mode, {})
    env_name = str(mode_config.get("url_env", "CHROME_CDP_URL"))
    return os.environ.get(env_name, str(mode_config.get("url", ""))).strip()


def is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/sys/kernel/osrelease").read_text().casefold()
    except OSError:
        return False


def windows_path(path: Path) -> str:
    result = subprocess.run(
        ["wslpath", "-w", str(path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result.stdout.strip()


def start_windows_job_chrome() -> str:
    """启动隔离的 Windows Chrome 配置，不更改 portproxy 状态。"""
    if not is_wsl():
        raise RuntimeError("仅可在 WSL 中自动启动 Windows Chrome")
    powershell = Path(
        "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    script = ROOT / "job_bot" / "scripts" / "windows" / "start-job-chrome.ps1"
    if not powershell.is_file():
        raise RuntimeError(f"未找到 PowerShell 可执行文件： {powershell}")
    if not script.is_file():
        raise RuntimeError(f"未找到 Chrome 启动脚本： {script}")
    result = subprocess.run(
        [
            str(powershell),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            windows_path(script),
        ],
        check=False,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=20,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"Windows Chrome 启动器运行失败： {detail}")
    return (result.stdout or "Chrome launch requested").strip()


def ensure_cdp(
    config: dict[str, Any], *, auto_start: bool, wait_seconds: float
) -> tuple[bool, str]:
    sources = enabled_cdp_sources(config)
    if not sources:
        return True, "没有启用的 CDP 来源"
    endpoint = cdp_endpoint(config)
    if not endpoint:
        return False, "CDP 端点为空"
    try:
        health = check_cdp_health(endpoint)
        return True, f"CDP 已就绪：{health.browser}"
    except CdpHealthError as first_error:
        if not auto_start:
            return False, str(first_error)
    try:
        start_windows_job_chrome()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return False, f"CDP 不可用且自动启动失败：{exc}"

    deadline = time.monotonic() + max(1.0, wait_seconds)
    last_error = "Chrome 未能就绪"
    while time.monotonic() < deadline:
        try:
            health = check_cdp_health(endpoint)
            return True, f"CDP 已自动启动：{health.browser}"
        except CdpHealthError as exc:
            last_error = str(exc)
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
    return (
        False,
        "Chrome 已启动，但 WSL CDP 端点仍不可用。"
        "可能需要修复 Windows portproxy 或防火墙。" + last_error,
    )


def load_state(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def current_run_results(database: Path, started_at: str) -> list[dict[str, Any]]:
    conn = connect_database(database)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT run.source_name, run.status, run.jobs_seen, run.jobs_new,
               run.error, run.started_at, run.finished_at
        FROM scan_runs AS run
        JOIN (
          SELECT source_name, MAX(id) AS id
          FROM scan_runs
          WHERE started_at >= ?
          GROUP BY source_name
        ) AS latest ON latest.id = run.id
        ORDER BY run.id
        """,
        (started_at,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def failure_category(error: str) -> str:
    normalized = " ".join(error.casefold().split())
    if any(
        marker in normalized
        for marker in (
            "http error 401",
            "http error 403",
            "http 401",
            "http 403",
            "authentication",
            "sign in",
            "login required",
            "captcha",
        )
    ):
        return "authentication_or_access"
    if transient_source_error(RuntimeError(normalized)) or any(
        marker in normalized
        for marker in ("http error 408", "http error 429", "http error 5")
    ):
        return "transient_retry_exhausted"
    return "adapter_or_layout"


def write_intervention_report(
    *,
    output_dir: Path,
    browser_message: str,
    skipped_cdp: list[str],
    run_results: list[dict[str, Any]],
    duration_seconds: int,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = local_now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"interventions_{stamp}.md"
    failures = [row for row in run_results if row.get("status") != "ok"]
    lines = [
        "# 每日扫描人工干预清单",
        "",
        f"生成时间：{local_now().isoformat(timespec='seconds')}",
        "",
        f"- 浏览器状态：{browser_message}",
        f"- 成功来源：{sum(row.get('status') == 'ok' for row in run_results)}",
        f"- 失败来源：{len(failures)}",
        f"- 总耗时：{duration_seconds // 60} 分 {duration_seconds % 60} 秒",
        "",
    ]
    if skipped_cdp:
        lines.extend(("## 因浏览器不可用而跳过", ""))
        lines.extend(f"- {name}" for name in skipped_cdp)
        lines.append("")
    groups: dict[str, list[dict[str, Any]]] = {
        "authentication_or_access": [],
        "transient_retry_exhausted": [],
        "adapter_or_layout": [],
    }
    for row in failures:
        groups[failure_category(str(row.get("error") or ""))].append(row)
    sections = (
        (
            "authentication_or_access",
            "登录或访问限制",
            "仅当登录确实过期时需要用户重新登录；否则由适配器排查访问策略。",
        ),
        (
            "transient_retry_exhausted",
            "可自动重试的瞬时故障",
            "机器人将在启用重试的后续任务中自行重试，当前无需人工重复运行。",
        ),
        (
            "adapter_or_layout",
            "适配器或页面结构异常",
            "需要维护代码或更新选择器，不要求用户反复刷新页面。",
        ),
    )
    for key, title, guidance in sections:
        rows = groups[key]
        if not rows:
            continue
        lines.extend((f"## {title}", "", guidance, ""))
        for row in rows:
            detail = " ".join(str(row.get("error") or "未知错误").split())[:500]
            lines.append(f"- {row['source_name']}：{detail}")
        lines.append("")
    if not failures:
        lines.extend(("## 抓取异常", "", "- 无。", ""))
    lines.extend(
        (
            "",
            "## 仍需人工完成的边界",
            "",
            "- 登录过期、MFA、验证码或网站明确要求真人确认。",
            "- 工作许可、签证、身份等无法从已确认资料唯一推出的回答。",
            "- 申请最终提交；机器人只准备到审核阶段。",
            "",
        )
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_strategy_report(
    database: Path, output_dir: Path, since: str, config_path: Path = DEFAULT_CONFIG
) -> tuple[Path, Path, str]:
    command = [
        sys.executable,
        str(ROOT / "job_bot" / "strategy_report.py"),
        "--config",
        str(config_path),
        "--db",
        str(database),
        "--out-dir",
        str(output_dir),
        "--since",
        since,
    ]
    result = subprocess.run(
        command, check=True, capture_output=True, text=True, timeout=60
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        raise RuntimeError("策略报告未返回输出路径")
    return Path(lines[0]), Path(lines[1]), lines[-1]


def run_weekly_report(
    database: Path, output_dir: Path, config_path: Path = DEFAULT_CONFIG
) -> tuple[Path, Path, str]:
    command = [
        sys.executable,
        str(ROOT / "job_bot" / "weekly_report.py"),
        "--config",
        str(config_path),
        "--db",
        str(database),
        "--out-dir",
        str(output_dir),
    ]
    result = subprocess.run(
        command, check=True, capture_output=True, text=True, timeout=60
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        raise RuntimeError("每周报告未返回输出路径")
    return Path(lines[0]), Path(lines[1]), lines[-1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="运行一次完整的每日岗位采集流程。"
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--since", help="ISO 基准时间；默认使用上次成功运行的时间")
    parser.add_argument("--max-workers", type=int)
    parser.add_argument("--no-browser-start", action="store_true")
    args = parser.parse_args()

    if args.env_file.is_file():
        load_env_file(args.env_file)
    config = load_config(args.config)
    state = load_state(args.state_file)
    started = utc_now().replace(microsecond=0)
    baseline = args.since or state.get("last_started_at")
    if not baseline:
        baseline = (started - dt.timedelta(hours=24)).isoformat()

    scan_config = config.get("scan", {})
    browser_ok, browser_message = ensure_cdp(
        config,
        auto_start=(
            not args.no_browser_start
            and bool(scan_config.get("auto_start_windows_chrome", True))
        ),
        wait_seconds=float(scan_config.get("browser_start_wait_seconds", 12)),
    )
    print(browser_message, flush=True)

    skipped_cdp: list[str] = []
    selected_sources: list[str] | None = None
    if not browser_ok:
        skipped_cdp = [str(source["name"]) for source in enabled_cdp_sources(config)]
        selected_sources = [
            str(source["name"])
            for source in config.get("sources", [])
            if source.get("enabled", True) is not False and not source_uses_cdp(source)
        ]
        print(
            f"继续扫描 HTTP 来源；已跳过 {len(skipped_cdp)} 个 CDP 来源",
            flush=True,
        )

    summary = scan(
        config,
        selected_sources=selected_sources,
        max_workers=args.max_workers,
    )
    rescored = rescore_jobs(config)
    database = db_path(config)
    run_results = current_run_results(database, started.isoformat())
    md_report, json_report, report_summary = run_strategy_report(
        database, JOBBOT_OUTPUT, str(baseline), args.config
    )
    penn_channels_report = None
    if browser_ok and config.get("penn_channels", {}).get("enabled", False):
        try:
            from job_bot.penn_channels import run as run_penn_channels
            run_penn_channels(config)
            penn_channels_report = str(JOBBOT_OUTPUT / "penn_channels/latest.md")
        except Exception as exc:
            print(f"宾大渠道刷新需要检查（{type(exc).__name__}）", flush=True)
    weekly_config = config.get("reporting", {}).get("weekly", {})
    weekly_md: Path | None = None
    weekly_json: Path | None = None
    weekly_summary = "weekly reporting disabled"
    if (
        weekly_config.get("enabled", True)
        and weekly_config.get("generate_after_daily", True)
    ):
        weekly_md, weekly_json, weekly_summary = run_weekly_report(
            database, JOBBOT_OUTPUT, args.config
        )
    completed_at = utc_now().replace(microsecond=0)
    completed = completed_at.isoformat()
    duration_seconds = int((completed_at - started).total_seconds())
    intervention_report = write_intervention_report(
        output_dir=JOBBOT_OUTPUT,
        browser_message=browser_message,
        skipped_cdp=skipped_cdp,
        run_results=run_results,
        duration_seconds=duration_seconds,
    )
    write_json_atomic(
        args.state_file,
        {
            "last_started_at": started.isoformat(),
            "last_completed_at": completed,
            "duration_seconds": duration_seconds,
            "baseline_used": baseline,
            "scan": summary,
            "rescored": rescored,
            "browser_ok": browser_ok,
            "strategy_report": str(md_report),
            "strategy_json": str(json_report),
            "weekly_report": str(weekly_md) if weekly_md else None,
            "weekly_json": str(weekly_json) if weekly_json else None,
            "penn_channels_report": penn_channels_report,
            "intervention_report": str(intervention_report),
        },
    )
    print(
        f"Scan: seen={summary['seen']} new={summary['new']}; rescored={rescored}; "
        f"duration={duration_seconds}s"
    )
    print(f"策略报告：{report_summary}")
    print(f"每周报告：{weekly_summary}")
    print(md_report)
    if weekly_md:
        print(weekly_md)
    print(intervention_report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

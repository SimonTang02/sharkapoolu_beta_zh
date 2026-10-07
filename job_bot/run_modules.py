#!/usr/bin/env python3
"""运行已配置的 job-bot 模块并记录可复现的运行清单。"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from job_bot.bot import load_config  # noqa: E402
from job_bot.source_selector import select_sources  # noqa: E402
from private_paths import JOBBOT_OUTPUT  # noqa: E402


DEFAULT_CONFIG = ROOT / "job_bot/config.china_hk_ic_foreign.json"
MODULES = {
    "penn_channels",
    "daily",
    "scan",
    "rescore",
    "digest",
    "scoring_report",
    "scoring_experiment",
    "strategy_full",
    "weekly",
    "session_audit",
}


def effective_config_hash(config: dict[str, Any]) -> str:
    serialized = json.dumps(
        config, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def merge_selector(
    base: dict[str, Any] | None, overlay: dict[str, Any]
) -> dict[str, Any]:
    merged = dict(base or {})
    for key, value in overlay.items():
        if value not in (None, []):
            merged[key] = value
    return merged


def module_command(
    module: str,
    *,
    config_path: Path,
    selector: dict[str, Any],
    config: dict[str, Any],
    max_workers: int | None,
) -> list[str]:
    from job_bot.operator_settings import require_module
    if module in ("daily", "scan", "penn_channels"):
        require_module(config, "scan")
    if module != "scan" and module != "session_audit":
        require_module(config, "report")
    if module == "session_audit":
        require_module(config, "sessions")
    python = sys.executable
    common = ["--config", str(config_path)]
    if module == "penn_channels":
        return [python, str(ROOT / "job_bot/penn_channels.py"), *common]
    if module == "daily":
        return [python, str(ROOT / "job_bot/daily_pipeline.py"), *common]
    if module == "scan":
        command = [python, str(ROOT / "job_bot/bot.py"), "scan", *common]
        selected = select_sources(config, selector)
        if not selected:
            raise ValueError("来源选择器未匹配到已启用的来源")
        for source in selected:
            command.extend(("--source", str(source["name"])))
        if max_workers is not None:
            command.extend(("--max-workers", str(max_workers)))
        return command
    if module == "rescore":
        return [python, str(ROOT / "job_bot/bot.py"), "rescore", *common]
    if module == "digest":
        return [python, str(ROOT / "job_bot/bot.py"), "digest", *common]
    if module == "scoring_report":
        return [python, str(ROOT / "job_bot/scoring_report.py"), *common]
    if module == "scoring_experiment":
        return [python, str(ROOT / "job_bot/scoring_experiment.py"), *common]
    if module == "strategy_full":
        return [python, str(ROOT / "job_bot/strategy_report.py"), *common]
    if module == "weekly":
        return [python, str(ROOT / "job_bot/weekly_report.py"), *common]
    if module == "session_audit":
        return [python, str(ROOT / "application_bot/session_audit.py"), *common]
    raise ValueError(f"未知模块： {module}")


def write_manifest(payload: dict[str, Any]) -> Path:
    JOBBOT_OUTPUT.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
    path = JOBBOT_OUTPUT / f"module_run_{stamp}.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--workflow", help="config.workflows 中定义的工作流名称")
    choice.add_argument(
        "--module", action="append", choices=sorted(MODULES), help="要运行的模块；可重复指定"
    )
    parser.add_argument("--source", action="append", help="来源的准确名称")
    parser.add_argument("--exclude-source", action="append", help="要排除的来源准确名称")
    parser.add_argument("--source-category", action="append")
    parser.add_argument("--source-type", action="append")
    parser.add_argument("--company", action="append")
    parser.add_argument(
        "--source-browser",
        choices=("any", "http", "cdp"),
        default=None,
        help="覆盖工作流的浏览器选择器；any 表示明确选择两者",
    )
    parser.add_argument("--max-workers", type=int)
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="写出计划但不执行模块")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    workflow: dict[str, Any] = {}
    if args.workflow:
        workflows = config.get("workflows", {})
        if args.workflow not in workflows:
            choices = ", ".join(sorted(workflows))
            raise SystemExit(f"未知工作流 {args.workflow!r}; available: {choices}")
        workflow = workflows[args.workflow]
        modules = [str(item) for item in workflow.get("modules", [])]
    else:
        modules = list(args.module or [])
    unknown = set(modules) - MODULES
    if unknown or not modules:
        raise SystemExit(f"模块列表无效或为空： {', '.join(sorted(unknown))}")

    cli_selector = {
        "include_names": args.source or [],
        "exclude_names": args.exclude_source or [],
        "include_categories": args.source_category or [],
        "include_types": args.source_type or [],
        "include_companies": args.company or [],
        "browser": args.source_browser,
    }
    selector = merge_selector(workflow.get("source_selector"), cli_selector)
    max_workers = (
        args.max_workers if args.max_workers is not None else workflow.get("max_workers")
    )
    if max_workers is not None and int(max_workers) < 1:
        raise SystemExit("--max-workers/workflow max_workers 必须至少为 1")
    continue_on_error = args.continue_on_error or bool(
        workflow.get("continue_on_error", False)
    )
    started = dt.datetime.now().astimezone()
    manifest: dict[str, Any] = {
        "started_at": started.isoformat(timespec="seconds"),
        "config_path": str(args.config.resolve()),
        "effective_config_sha256": effective_config_hash(config),
        "experiment": config.get("experiment"),
        "workflow": args.workflow,
        "modules": modules,
        "source_selector": selector,
        "dry_run": args.dry_run,
        "results": [],
    }
    exit_code = 0
    for module in modules:
        command = module_command(
            module,
            config_path=args.config.resolve(),
            selector=selector,
            config=config,
            max_workers=int(max_workers) if max_workers is not None else None,
        )
        result_record: dict[str, Any] = {"module": module, "command": command}
        if args.dry_run:
            result_record["status"] = "planned"
            manifest["results"].append(result_record)
            continue
        print(f"[{module}] 开始运行", flush=True)
        before = time.monotonic()
        result = subprocess.run(command, cwd=ROOT, check=False, text=True)
        result_record.update(
            {
                "status": "ok" if result.returncode == 0 else "error",
                "returncode": result.returncode,
                "duration_seconds": round(time.monotonic() - before, 3),
            }
        )
        manifest["results"].append(result_record)
        if result.returncode:
            exit_code = result.returncode
            if not continue_on_error:
                break
    manifest["completed_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    path = write_manifest(manifest)
    print(path)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

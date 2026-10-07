#!/usr/bin/env python3
"""规划或运行已配置的禁止提交申请适配器。"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from application_bot.portal_registry import (  # noqa: E402
    adapter_command,
    resolve_adapter,
    resolve_company_profile,
)
from application_bot.application_limits import check_application_limit  # noqa: E402
from job_bot.bot import connect_db, load_config  # noqa: E402
from private_paths import APPLICATION_OUTPUT, CREDENTIALS_FILE  # noqa: E402


DEFAULT_CONFIG = ROOT / "job_bot" / "config.china_hk_ic_foreign.json"


@dataclass
class DispatchPlan:
    application_id: int
    company: str
    title: str
    platform: str
    application_status: str
    adapter: str | None
    company_profile: str | None
    state: str
    supports_draft: bool
    timeout_seconds: int
    command: list[str] | None
    result_code: int | None = None
    error: str = ""
    application_limit: dict | None = None


def application_rows(conn, application_ids: list[int], campaign_id: int | None):
    if application_ids:
        placeholders = ",".join("?" for _ in application_ids)
        return conn.execute(
            f"""
            SELECT applications.id, applications.status, jobs.company,
                   jobs.title, jobs.platform, jobs.url
            FROM applications JOIN jobs ON jobs.id=applications.job_id
            WHERE applications.id IN ({placeholders})
            ORDER BY applications.id
            """,
            application_ids,
        ).fetchall()
    return conn.execute(
        """
        SELECT applications.id, applications.status, jobs.company,
               jobs.title, jobs.platform, jobs.url
        FROM application_campaign_jobs
        JOIN applications ON applications.id=application_campaign_jobs.application_id
        JOIN jobs ON jobs.id=applications.job_id
        WHERE application_campaign_jobs.campaign_id=?
          AND applications.status NOT IN ('submitted', 'cancelled')
        ORDER BY application_campaign_jobs.rank
        """,
        (campaign_id,),
    ).fetchall()


def build_plans(
    config, rows, config_path: Path, env_path: Path, limit_checks: dict | None = None
) -> list[DispatchPlan]:
    plans = []
    for row in rows:
        adapter = resolve_adapter(
            config,
            company=str(row["company"] or ""),
            platform=str(row["platform"] or ""),
            url=str(row["url"] or ""),
        )
        command = (
            adapter_command(
                adapter,
                application_id=int(row["id"]),
                config_path=config_path,
                env_path=env_path,
            )
            if adapter
            else None
        )
        limit_check = (limit_checks or {}).get(int(row["id"]))
        state = "ready" if command else "manual_or_unsupported"
        if limit_check and limit_check.state in (
            "application_limit_reached", "category_review_required"
        ):
            state = limit_check.state
            command = None
        elif limit_check and limit_check.state == "limit_advisory":
            state = "ready_with_limit_advisory" if command else "limit_advisory"
        company_profile = (
            resolve_company_profile(
                config,
                company=str(row["company"] or ""),
                adapter_id=adapter.id,
            )
            if adapter
            else {}
        )
        plans.append(
            DispatchPlan(
                application_id=int(row["id"]),
                company=str(row["company"] or ""),
                title=str(row["title"] or ""),
                platform=str(row["platform"] or ""),
                application_status=str(row["status"] or ""),
                adapter=adapter.id if adapter else None,
                company_profile=str(company_profile.get("id")) if company_profile else None,
                state=state,
                supports_draft=adapter.supports_draft if adapter else False,
                timeout_seconds=adapter.timeout_seconds if adapter else 0,
                command=command,
                application_limit=asdict(limit_check) if limit_check else None,
            )
        )
    return plans


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    targets = parser.add_mutually_exclusive_group(required=True)
    targets.add_argument("--application-id", action="append", type=int, default=[])
    targets.add_argument("--campaign-id", type=int)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--env-file", type=Path, default=CREDENTIALS_FILE)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="依次运行已配置的禁止提交适配器；默认只写入计划。",
    )
    args = parser.parse_args()
    config = load_config(args.config)
    if config.get("field_mappings", {}).get("safety", {}).get("allow_submit") is not False:
        raise SystemExit("配置项 safety.allow_submit 必须为 false")
    conn = connect_db(config)
    rows = application_rows(conn, args.application_id, args.campaign_id)
    limit_checks = {
        int(row["id"]): check_application_limit(conn, int(row["id"]))
        for row in rows
    }
    conn.close()
    plans = build_plans(config, rows, args.config, args.env_file, limit_checks)
    policy = config.get("operator_controls")
    if policy:
        from job_bot.operator_settings import require_module, region_allowed
        from application_bot.operator_review import validate_application_review
        require_module(config, "fill")
        conn = connect_db(config)
        try:
            for plan in plans:
                location = conn.execute("SELECT jobs.location FROM jobs JOIN applications ON applications.job_id=jobs.id WHERE applications.id=?", (plan.application_id,)).fetchone()
                if plan.application_status in ("submitted", "cancelled"):
                    plan.state, plan.command = "already_finished", None
                elif not location or not region_allowed(location[0], config):
                    plan.state, plan.command = "region_disabled", None
                elif policy["mode"] == "manual":
                    plan.state, plan.command = "manual_assist_required", None
                elif plan.command:
                    try:
                        validate_application_review(conn, plan.application_id, policy["review_rounds"])
                    except ValueError as exc:
                        plan.state, plan.command, plan.error = "review_required", None, str(exc)
                plan.timeout_seconds = min(plan.timeout_seconds, policy["job_timeout_seconds"]) if plan.timeout_seconds else 0
        finally:
            conn.close()

    if args.execute and policy:
        from job_bot.operator_browser import ApplicationMonitor
        from application_bot.operator_execution import execute_preparation
        if any(plan.command for plan in plans):
            monitor = ApplicationMonitor(config)
            try:
                stopped = False
                for plan in plans:
                    if stopped and plan.command:
                        plan.state, plan.command = "batch_stopped_for_manual_checkpoint", None
                    if not plan.command:
                        continue
                    execute_preparation(plan, config, monitor, ROOT)
                    if plan.result_code and policy["on_interruption"] == "stop":
                        stopped = True
            finally:
                monitor.close()
    elif args.execute:
        for plan in plans:
            if not plan.command:
                continue
            try:
                result = subprocess.run(
                    plan.command,
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=plan.timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                plan.result_code = 124
                plan.state = "adapter_timeout"
                output = exc.stderr or exc.stdout or ""
                if isinstance(output, bytes):
                    output = output.decode("utf-8", errors="replace")
                detail = " ".join(str(output).split())[-350:]
                plan.error = (
                    f"Adapter exceeded {plan.timeout_seconds}s timeout"
                    + (f": {detail}" if detail else "")
                )
                continue
            plan.result_code = result.returncode
            if result.returncode:
                plan.state = "adapter_failed"
                plan.error = " ".join((result.stderr or result.stdout).split())[-500:]
            else:
                plan.state = "adapter_completed"

    APPLICATION_OUTPUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = APPLICATION_OUTPUT / f"dispatch_plan_{stamp}.json"
    path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "executed": args.execute,
                "final_submit_allowed": False,
                "plans": [asdict(plan) for plan in plans],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(path)
    print(
        f"planned={len(plans)} ready={sum(plan.command is not None for plan in plans)} "
        f"executed={sum(plan.result_code is not None for plan in plans)}"
    )
    if args.execute and policy:
        return 0 if plans and all(plan.state in ("adapter_prepared_not_submitted", "already_finished") for plan in plans) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

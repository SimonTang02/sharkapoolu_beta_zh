#!/usr/bin/env python3
"""管理申请队列并准备申请材料；必须由人工批准。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_PACKAGES = ROOT / ".python_packages"
if LOCAL_PACKAGES.is_dir() and str(LOCAL_PACKAGES) not in sys.path:
    sys.path.insert(0, str(LOCAL_PACKAGES))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from job_bot.applications.nvidia_workday import _playwright_api, run_preview  # noqa: E402
from job_bot.browser_connection import check_cdp_health  # noqa: E402
from job_bot.bot import connect_db, load_config, load_env_file, utc_now  # noqa: E402
from application_bot.profile_policy import apply_explicit_authorization  # noqa: E402
from application_bot.portal_registry import resolve_company_profile  # noqa: E402
from cv.application_keywords import select_keywords, apply_keyword_selection  # noqa: E402
from private_paths import (  # noqa: E402
    APPLICATION_PROFILE,
    APPLICATION_OUTPUT,
    BROWSER_PROFILE_DIR,
    BROWSER_STATE_DIR,
    CREDENTIALS_FILE,
    CURRENT_RESUME_PDF,
    CURRENT_RESUME_TEX,
    JOBBOT_OUTPUT,
)
from job_bot.private_config import validate_application_profile  # noqa: E402


DEFAULT_CONFIG = ROOT / "job_bot" / "config.china_hk_ic_foreign.json"
DEFAULT_PROFILE = APPLICATION_PROFILE


def resolve_browser_connection(
    config: dict,
    *,
    mode_override: str | None = None,
    cdp_url_override: str | None = None,
) -> tuple[str, str]:
    browser_config = config.get("application_browser", {})
    mode = mode_override or browser_config.get("mode", "local_persistent")
    if cdp_url_override and not mode_override:
        mode = "windows_cdp"
    if mode == "local_persistent":
        if cdp_url_override:
            raise SystemExit("--cdp-url 不能与 --browser-mode local_persistent 同时使用")
        return mode, ""
    if mode != "windows_cdp":
        raise SystemExit(
            "application_browser.mode 必须为 local_persistent 或 windows_cdp"
        )

    cdp_config = browser_config.get("windows_cdp", {})
    url_env = cdp_config.get("url_env", "CHROME_CDP_URL")
    cdp_url = (
        (cdp_url_override or "").strip()
        or os.environ.get(url_env, "").strip()
        or str(cdp_config.get("url", "")).strip()
    )
    if not cdp_url:
        raise SystemExit(
            f"已选择 windows_cdp，但未配置端点；请设置 {url_env} "
            "或配置 application_browser.windows_cdp.url"
        )
    if not cdp_url.startswith(("http://", "https://", "ws://", "wss://")):
        raise SystemExit("Chrome CDP 端点必须是 HTTP(S) 或 WebSocket URL")
    return mode, cdp_url


def add_event(conn, application_id: int, event_type: str, details: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO application_events(application_id, event_type, details_json) VALUES (?, ?, ?)",
        (application_id, event_type, json.dumps(details or {}, ensure_ascii=False)),
    )


def cmd_queue(args: argparse.Namespace) -> None:
    config = load_config(Path(args.config))
    conn = connect_db(config)
    if args.job_id:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (args.job_id,)).fetchone()
    else:
        job = conn.execute("SELECT * FROM jobs WHERE url = ?", (args.job_url,)).fetchone()
    if not job:
        raise SystemExit("本地数据库中未找到该职位；请先扫描职位，再将其加入队列")
    existing = conn.execute(
        "SELECT id, status FROM applications WHERE job_id = ? ORDER BY id DESC LIMIT 1",
        (job["id"],),
    ).fetchone()
    if existing and existing["status"] not in {"failed", "cancelled"}:
        print(f"申请已存在：id={existing['id']} status={existing['status']}")
        return
    now = utc_now()
    application_id = conn.execute(
        """
        INSERT INTO applications(
          job_id, status, tailored_resume_path, profile_path, notes, created_at, updated_at
        ) VALUES (?, 'queued', ?, ?, ?, ?, ?)
        """,
        (job["id"], args.resume, args.profile, args.notes, now, now),
    ).lastrowid
    add_event(conn, application_id, "queued", {"job_url": job["url"]})
    conn.commit()
    print(f"已加入申请队列 id={application_id}：{job['title']} — {job['company']}")


def cmd_list(args: argparse.Namespace) -> None:
    config = load_config(Path(args.config))
    conn = connect_db(config)
    rows = conn.execute(
        """
        SELECT applications.id, applications.status, jobs.title, jobs.company, jobs.url,
               applications.updated_at
        FROM applications JOIN jobs ON jobs.id = applications.job_id
        ORDER BY applications.id DESC
        """
    ).fetchall()
    if not rows:
        print("申请队列为空")
        return
    for row in rows:
        print(f"{row['id']:4d} | {row['status']:20s} | {row['title']} | {row['company']} | {row['url']}")


def load_profile(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(
            f"未找到申请资料：{path}.。请运行 "
            "'python3 -m job_bot.private_config init' 并填写私有资料。"
        )
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"申请资料 JSON 无效，错误位于第 {exc.lineno}: {path}") from exc
    if not isinstance(profile, dict):
        raise SystemExit(f"申请资料的根节点必须是对象： {path}")
    errors = [
        issue
        for issue in validate_application_profile(profile, str(path))
        if issue.level == "ERROR"
    ]
    if errors:
        summary = "; ".join(f"{issue.path}: {issue.message}" for issue in errors)
        raise SystemExit(f"申请资料无效： {summary}")
    return profile


def hydrate_known_resume_contacts(profile: dict, resume_tex: str) -> dict:
    """仅填写简历中明确提供且当前为空的联系信息字段。"""
    fields = profile.setdefault("fields", {})
    name_match = re.search(r"\\name\{([^}]+)\}", resume_tex)
    if name_match:
        formal, _, preferred = name_match.group(1).partition(",")
        name_parts = formal.strip().split()
        known = {
            "first_name": name_parts[0] if name_parts else "",
            "last_name": " ".join(name_parts[1:]).title() if len(name_parts) > 1 else "",
            "preferred_name": preferred.strip(),
        }
        for key, value in known.items():
            if value and not fields.get(key):
                fields[key] = value
    email_match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", resume_tex)
    if email_match and not fields.get("email"):
        fields["email"] = email_match.group(0)
    us_phone = re.search(r"US:\s*([+\d][\d ()-]+)", resume_tex)
    hk_phone = re.search(r"HK:\s*([+\d][\d ()-]+)", resume_tex)
    phone_match = us_phone or hk_phone
    if phone_match and not fields.get("phone"):
        fields["phone"] = phone_match.group(1).strip()
    linkedin = re.search(r"\\href\{(https?://(?:www\.)?linkedin\.com/[^}]+)\}", resume_tex)
    if linkedin and not fields.get("linkedin_url"):
        fields["linkedin_url"] = linkedin.group(1)
    return profile


def cmd_prepare_profile(args: argparse.Namespace) -> None:
    config = load_config(Path(args.config))
    conn = connect_db(config)
    row = conn.execute(
        "SELECT applications.id, jobs.title, jobs.description FROM applications "
        "JOIN jobs ON jobs.id = applications.job_id WHERE applications.id = ?", (args.application_id,)
    ).fetchone()
    if not row:
        raise SystemExit(f"未找到申请记录： {args.application_id}")
    base_path = Path(args.base_profile)
    if not base_path.is_absolute():
        base_path = ROOT / base_path
    profile = hydrate_known_resume_contacts(
        load_profile(base_path),
        CURRENT_RESUME_TEX.read_text(encoding="utf-8"),
    )
    profile = apply_keyword_selection(profile, select_keywords(row["title"] or "", row["description"] or ""))
    resume_path = Path(args.resume).resolve()
    cover_path = Path(args.cover_letter).resolve()
    if not resume_path.is_file() or not cover_path.is_file():
        raise SystemExit("定制简历和求职信文件都必须存在")
    profile.setdefault("documents", {})["resume_path"] = str(resume_path)
    profile["documents"]["cover_letter_path"] = str(cover_path)
    safety = profile.setdefault("safety", {})
    safety["allow_submit"] = False
    safety["allow_sensitive_answers"] = False
    output_dir = APPLICATION_OUTPUT / "applications" / str(args.application_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    profile_path = output_dir / "profile.json"
    profile_path.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    profile_path.chmod(0o600)
    now = utc_now()
    conn.execute(
        """
        UPDATE applications
        SET tailored_resume_path = ?, cover_letter_path = ?, profile_path = ?, updated_at = ?
        WHERE id = ?
        """,
        (str(resume_path), str(cover_path), str(profile_path), now, args.application_id),
    )
    add_event(
        conn,
        args.application_id,
        "materials_prepared",
        {"resume_path": str(resume_path), "cover_letter_path": str(cover_path)},
    )
    conn.commit()
    print(f"已为申请 {args.application_id} 准备独立的材料和资料。")


def cmd_authorize_profile(args: argparse.Namespace) -> None:
    profile_path = Path(args.profile)
    if not profile_path.is_absolute():
        profile_path = ROOT / profile_path
    profile = load_profile(profile_path)
    apply_explicit_authorization(
        profile,
        gender=args.gender,
        work_authorized=args.work_authorized == "yes",
        sponsorship_required=args.sponsorship == "yes",
        scopes=["mainland_china", "hong_kong"],
        amd_privacy_accepted=args.amd_privacy_accepted,
    )
    profile_path.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    profile_path.chmod(0o600)
    print("已保存明确提供的申请答案；最终提交仍处于禁用状态。")


def cmd_workday_preview(args: argparse.Namespace) -> None:
    config = load_config(Path(args.config))
    browser_mode, cdp_url = resolve_browser_connection(
        config,
        mode_override=args.browser_mode,
        cdp_url_override=args.cdp_url,
    )
    if config.get("application_browser", {}).get("auto_submit", False):
        raise SystemExit(
            "application_browser.auto_submit 必须保持为 false；系统未实现最终提交"
        )
    if browser_mode == "windows_cdp":
        health = check_cdp_health(cdp_url)
        cdp_url = health.connect_url
        print(f"专用 Chrome 已就绪：{health.browser}")
    conn = connect_db(config)
    row = conn.execute(
        """
        SELECT applications.*, jobs.title, jobs.company, jobs.url, jobs.location, jobs.description
        FROM applications JOIN jobs ON jobs.id = applications.job_id
        WHERE applications.id = ?
        """,
        (args.application_id,),
    ).fetchone()
    if not row:
        raise SystemExit(f"未找到申请记录： {args.application_id}")
    hostname = (urllib.parse.urlsplit(row["url"] or "").hostname or "").lower()
    if not hostname.endswith("myworkdayjobs.com"):
        raise SystemExit("workday-preview 仅支持 Workday 职位 URL")
    company_slug = re.sub(r"[^A-Za-z0-9]+", "_", row["company"] or "workday").strip("_").upper()
    tenant_slug = re.sub(r"[^A-Za-z0-9]+", "_", hostname).strip("_").lower()

    profile_path = Path(args.profile or row["profile_path"] or DEFAULT_PROFILE)
    if not profile_path.is_absolute():
        profile_path = ROOT / profile_path
    profile = load_profile(profile_path)
    profile = apply_keyword_selection(profile, select_keywords(row["title"] or "", row["description"] or ""))
    company_profile = resolve_company_profile(
        config, company=str(row["company"] or ""), adapter_id="workday"
    )
    output_dir = JOBBOT_OUTPUT / "applications" / str(args.application_id)
    browser_profile_dir = BROWSER_PROFILE_DIR / "workday" / tenant_slug
    storage_env = f"COMPANY_{company_slug}_STORAGE_STATE"
    raw_browser_state = os.environ.get(storage_env, "").strip()
    browser_state_path = (
        Path(raw_browser_state)
        if raw_browser_state
        else BROWSER_STATE_DIR / "workday" / f"{tenant_slug}.json"
    )
    if not browser_state_path.is_absolute():
        browser_state_path = ROOT / browser_state_path
    now = utc_now()
    try:
        report = run_preview(
            job_url=row["url"],
            job_location=str(row["location"] or ""),
            cookie_header=(
                os.environ.get(f"COMPANY_{company_slug}_COOKIE", "")
                or os.environ.get(f"COMPANY_{company_slug}_SESSION", "")
            ),
            profile=profile,
            project_root=ROOT,
            browser_profile_dir=browser_profile_dir,
            browser_state_path=browser_state_path,
            output_dir=output_dir,
            headless=args.headless,
            start_application=args.start_application,
            apply_mode=args.apply_mode,
            advance_one_step=args.advance_one_step,
            advance_to_review=args.advance_to_review,
            save_draft=args.save_draft,
            interactive=args.interactive,
            cdp_url=cdp_url if browser_mode == "windows_cdp" else "",
            cookie_origin=f"{urllib.parse.urlsplit(row['url']).scheme}://{hostname}",
            login_username=os.environ.get(f"COMPANY_{company_slug}_USERNAME", ""),
            login_password=os.environ.get(f"COMPANY_{company_slug}_PASSWORD", ""),
            application_id=args.application_id,
            registry_conn=conn,
            company_rules=company_profile.get("rules", {}),
        )
        if report.get("authentication_required"):
            status = "authentication_required"
        elif report.get("manual_intervention"):
            status = "manual_action_required"
        elif report.get("validation_blocked"):
            status = "validation_blocked"
        elif report["server_draft_saved"]:
            status = "draft_saved"
        elif report["application_started"]:
            status = "awaiting_review"
        else:
            status = "previewed"
        conn.execute(
            """
            UPDATE applications
            SET status = ?, profile_path = ?, browser_state_path = ?, draft_url = ?,
                field_report_json = ?, last_error = NULL, updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                str(profile_path),
                str(browser_state_path) if report.get("browser_state_saved") else None,
                report["final_url"],
                json.dumps(report, ensure_ascii=False),
                now,
                args.application_id,
            ),
        )
        add_event(conn, args.application_id, status, report)
        conn.commit()
        print(f"Workday 申请 {args.application_id}（{row['company']}）：{status}")
        print(f"预览：{report['screenshot_path']}")
        print(f"字段报告：{output_dir / 'field_report.json'}")
        print("未执行最终提交。")
    except Exception as exc:
        conn.execute(
            "UPDATE applications SET status = 'failed', last_error = ?, updated_at = ? WHERE id = ?",
            (str(exc), now, args.application_id),
        )
        add_event(conn, args.application_id, "failed", {"error": str(exc)})
        conn.commit()
        raise


def cmd_nvidia_preview(args: argparse.Namespace) -> None:
    """仅适用于 NVIDIA 的向后兼容入口。"""
    config = load_config(Path(args.config))
    conn = connect_db(config)
    row = conn.execute(
        """
        SELECT jobs.company, jobs.url
        FROM applications JOIN jobs ON jobs.id = applications.job_id
        WHERE applications.id = ?
        """,
        (args.application_id,),
    ).fetchone()
    if not row:
        raise SystemExit(f"未找到申请记录： {args.application_id}")
    if "nvidia" not in (row["company"] or "").lower() and "nvidia" not in (row["url"] or "").lower():
        raise SystemExit("nvidia-preview 仅支持 NVIDIA Workday 职位；其他情况请使用 workday-preview")
    cmd_workday_preview(args)


def cmd_browser_health(args: argparse.Namespace) -> None:
    config = load_config(Path(args.config))
    mode, cdp_url = resolve_browser_connection(
        config,
        mode_override=args.browser_mode,
        cdp_url_override=args.cdp_url,
    )
    if mode != "windows_cdp":
        raise SystemExit(
            "请将 application_browser.mode 设为 windows_cdp，或传入 "
            "--browser-mode windows_cdp"
        )
    health = check_cdp_health(cdp_url)
    print(f"专用 Chrome CDP 运行正常：{health.browser}")


def cmd_browser_smoke(args: argparse.Namespace) -> None:
    config = load_config(Path(args.config))
    mode, cdp_url = resolve_browser_connection(
        config,
        mode_override=args.browser_mode,
        cdp_url_override=args.cdp_url,
    )
    if mode != "windows_cdp":
        raise SystemExit("浏览器冒烟检查要求使用 windows_cdp 模式")
    health = check_cdp_health(cdp_url)
    sync_playwright = _playwright_api()
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(
            health.connect_url,
            timeout=30_000,
        )
        if not browser.contexts:
            raise RuntimeError("已连接的 Chrome 未提供可用的浏览器上下文")
        page = browser.contexts[0].new_page()
        try:
            response = page.goto(
                args.url,
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            if response is not None and response.status >= 400:
                raise RuntimeError(f"冒烟检查页面返回 HTTP {response.status}")
            title = page.title()
        finally:
            page.close()
        # 不要调用 browser.close()：这是由外部管理的 Windows Chrome。
    after = check_cdp_health(cdp_url)
    print(
        f"专用 Chrome 冒烟检查通过：{after.browser}；"
        f"已打开并关闭一个自动化标签页（{title or args.url}）"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="需经人工批准的申请准备工具")
    sub = parser.add_subparsers(required=True)

    queue = sub.add_parser("queue", help="将本地职位数据库中的岗位加入申请队列")
    target = queue.add_mutually_exclusive_group(required=True)
    target.add_argument("--job-id", type=int)
    target.add_argument("--job-url")
    queue.add_argument("--config", default=str(DEFAULT_CONFIG))
    queue.add_argument("--profile", default=str(DEFAULT_PROFILE))
    queue.add_argument("--resume", default=str(CURRENT_RESUME_PDF))
    queue.add_argument("--notes", default="")
    queue.set_defaults(func=cmd_queue)

    list_parser = sub.add_parser("list", help="列出本地申请队列")
    list_parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    list_parser.set_defaults(func=cmd_list)

    prepare = sub.add_parser(
        "prepare-profile",
        help="将已审阅的简历和求职信 PDF 关联到独立申请资料",
    )
    prepare.add_argument("--application-id", type=int, required=True)
    prepare.add_argument("--resume", required=True)
    prepare.add_argument("--cover-letter", required=True)
    prepare.add_argument("--base-profile", default=str(DEFAULT_PROFILE))
    prepare.add_argument("--config", default=str(DEFAULT_CONFIG))
    prepare.set_defaults(func=cmd_prepare_profile)

    authorize = sub.add_parser(
        "authorize-profile",
        help="保存候选人明确提供且适用于中国/香港地区的敏感问题答案",
    )
    authorize.add_argument("--profile", default=str(DEFAULT_PROFILE))
    authorize.add_argument("--gender", choices=("male", "female", "decline"), required=True)
    authorize.add_argument("--work-authorized", choices=("yes", "no"), required=True)
    authorize.add_argument("--sponsorship", choices=("yes", "no"), required=True)
    authorize.add_argument("--amd-privacy-accepted", action="store_true")
    authorize.set_defaults(func=cmd_authorize_profile)

    def add_workday_preview(name: str, help_text: str, handler: object) -> None:
        preview = sub.add_parser(name, help=help_text)
        preview.add_argument("--application-id", type=int, required=True)
        preview.add_argument("--config", default=str(DEFAULT_CONFIG))
        preview.add_argument("--env-file", default=str(CREDENTIALS_FILE))
        preview.add_argument("--profile")
        preview.add_argument("--headless", action="store_true")
        preview.add_argument("--start-application", action="store_true")
        preview.add_argument("--apply-mode", choices=("resume", "manual", "last"), default="resume")
        preview.add_argument("--advance-one-step", action="store_true")
        preview.add_argument("--advance-to-review", action="store_true")
        preview.add_argument("--save-draft", action="store_true")
        preview.add_argument("--interactive", action="store_true")
        preview.add_argument(
            "--cdp-url",
            help="临时覆盖专用 Chrome CDP 端点",
        )
        preview.add_argument(
            "--browser-mode",
            choices=("local_persistent", "windows_cdp"),
            help="临时覆盖配置中的 application_browser.mode",
        )
        preview.set_defaults(func=handler)

    add_workday_preview(
        "workday-preview",
        "打开并准备受支持的 Workday 申请",
        cmd_workday_preview,
    )
    add_workday_preview(
        "nvidia-preview",
        "向后兼容的 NVIDIA Workday 申请命令",
        cmd_nvidia_preview,
    )

    health = sub.add_parser(
        "browser-health",
        help="检查已配置的专用 Windows Chrome CDP 端点",
    )
    health.add_argument("--config", default=str(DEFAULT_CONFIG))
    health.add_argument("--env-file", default=str(CREDENTIALS_FILE))
    health.add_argument("--cdp-url")
    health.add_argument(
        "--browser-mode",
        choices=("local_persistent", "windows_cdp"),
    )
    health.set_defaults(func=cmd_browser_health)

    smoke = sub.add_parser(
        "browser-smoke",
        help="在专用 Windows Chrome 中打开并关闭一个由自动化拥有的标签页",
    )
    smoke.add_argument("--config", default=str(DEFAULT_CONFIG))
    smoke.add_argument("--env-file", default=str(CREDENTIALS_FILE))
    smoke.add_argument("--cdp-url")
    smoke.add_argument(
        "--browser-mode",
        choices=("local_persistent", "windows_cdp"),
    )
    smoke.add_argument("--url", default="https://example.com")
    smoke.set_defaults(func=cmd_browser_smoke)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    env_file = getattr(args, "env_file", None)
    if env_file:
        load_env_file(Path(env_file))
    args.func(args)


if __name__ == "__main__":
    main()

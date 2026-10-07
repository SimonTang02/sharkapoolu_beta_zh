"""面向中文初学者的控制界面：明确展示计划，执行需主动启用。"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys

from private_paths import (APPLICATION_OUTPUT, APPLICATION_PROFILE, EVIDENCE_PROFILE, CREDENTIALS_FILE,
    APPLICATION_KEYWORDS, EASY_SETTINGS, EASY_RUNTIME_CONFIG, PRIVATE_CONFIG, PRIVATE_ROOT, PROJECT_ROOT)
from job_bot.operator_settings import compile_settings, load_settings, require_module, region_allowed
from job_bot.bot import load_config, connect_db, load_env_file
from job_bot.config_loader import validate_config

TASKS = ("scan", "assist", "fill", "materials", "manual_kit", "report", "sessions")


def write_private(path: Path, data: str):
    if not path.resolve().is_relative_to(PRIVATE_ROOT.resolve()):
        raise ValueError("输出只能写入私有目录")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(data, encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def prepare_config(settings_path: Path, base_path: Path) -> dict:
    config = compile_settings(load_config(base_path), load_settings(settings_path))
    validate_config(config)
    return config


def task_command(task: str, ids: list[int], manifest: Path | None, output: Path | None) -> list[str] | None:
    common = ["--config", str(EASY_RUNTIME_CONFIG)]
    if task == "scan":
        return [sys.executable, "-m", "job_bot.bot", "scan", *common]
    if task == "report":
        return [sys.executable, "-m", "job_bot.bot", "digest", *common, "--print"]
    if task == "sessions":
        return [sys.executable, "-m", "application_bot.session_audit", *common]
    if task == "fill":
        if not ids:
            raise ValueError("填写任务必须提供 --application-id，可重复指定")
        return [sys.executable, "-m", "application_bot.dispatcher", *common,
                *[value for appid in ids for value in ("--application-id", str(appid))]]
    if task == "manual_kit" and manifest and output:
        return [sys.executable, "-m", "application_bot.manual_kit", "--manifest", str(manifest), "--output", str(output)]
    return None


def agent_packet(config: dict, task: str, ids: list[int]) -> Path:
    from application_bot.operator_review import review_path, review_template, job_fingerprint
    policy = config["operator_controls"]
    if task in ("assist", "materials") and not ids:
        raise ValueError("智能体辅助或材料任务必须提供 --application-id")
    rows = []
    if ids:
        conn = connect_db(config)
        try:
            for appid in ids:
                row = conn.execute("SELECT applications.id,applications.status,jobs.company,jobs.title,jobs.url,jobs.location,jobs.description,applications.profile_path FROM applications JOIN jobs ON jobs.id=applications.job_id WHERE applications.id=?", (appid,)).fetchone()
                if row is None:
                    raise ValueError("数据库中不存在指定投递编号")
                if row["status"] in ("submitted", "cancelled"):
                    raise ValueError("指定岗位已提交或已取消，不再生成填写任务")
                if not region_allowed(row["location"], config):
                    raise ValueError("指定岗位的地区已关闭")
                rows.append(dict(row))
        finally:
            conn.close()
    folder = APPLICATION_OUTPUT / f"agent_task_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    write_private(folder / "targets.json", json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    for appid in ids:
        path = review_path(appid)
        if not path.exists():
            review = review_template(appid, policy["review_rounds"])
            row = next(row for row in rows if row["id"] == appid)
            review["job_fingerprint"] = job_fingerprint(row["url"], row["description"])
            write_private(path, json.dumps(review, ensure_ascii=False, indent=2) + "\n")
    if task == "assist" and policy["capture_assist"]:
        from job_bot.operator_browser import ApplicationMonitor
        monitor = ApplicationMonitor(config)
        try:
            for appid in ids:
                if not monitor.screenshot(appid, folder / f"application_{appid}.png"):
                    raise ValueError("没有找到对应的已注册投递标签页；请让Agent先登记专用标签页")
        finally:
            monitor.close()
    text = f"""# 新对话任务包

任务：{task}。先读项目 AGENTS.md、AGENT_HANDOFF.md、docs/beginner-settings.md，
材料或事实缺失时再按 docs/candidate-onboarding.md 与 docs/manual-database.md 核对；
这是既有投递任务，不应因此重置资料或重新导入已审阅简历。
再读本文件旁的 targets.json、配置 {EASY_SETTINGS} 和运行配置 {EASY_RUNTIME_CONFIG}。
实际数据库通过运行配置和 shared_database 接口读取；不要把数据库复制到新路径。

个人答案：{APPLICATION_PROFILE}
技能和资格证据：{EVIDENCE_PROFILE}
材料关键词：{APPLICATION_KEYWORDS}

依据当前岗位JD、数据库和本人已确认的事实协助；未知资格、签证、毕业后实习、
来源选项、法律声明和必填问题必须询问本人。不得生成个人事实。
图片辨识由当前Agent执行；目录中有截图则逐图检查，否则读取专用投递页或让本人提供截图。
此入口不调用付费模型API，也不会自行启动或购买Agent服务。

执行开关以 operator_controls.modules 为准，关闭的模块不能因任务包而开启。
materials 仅允许生成材料提案并视觉审阅；manual_kit 使用已审阅Manifest和PDF，
不得替换已交付附件、覆盖投递包或重置浏览器进度。

每岗独立完成 {policy['review_rounds']} 轮审查，每轮逐项核对 qualification、fields、attachments。
审查记录：{APPLICATION_OUTPUT / 'operator_reviews'} / application_<id>.json。
填 reviewer、reviewed_at、逐项说明和 evidence 数组（path 为私有文件绝对路径，
sha256 为该文件真实哈希）；只有实际核对通过才能把 passed 改为 true。
资格证据须包含当前JD/个人资格；字段须包含当前答案/表单证据；附件必须包含
实际上传PDF及视觉审阅说明。现存审查记录不可覆盖；有变化则明确重新审阅。
qualification 的 evidence 须包含上述个人答案和技能证据两个文件；fields 须包含
主库 profile_path 指向的逐岗档案（无绑定时为个人答案文件）；attachments 须包含
该档案 documents 中实际使用的 resume_path 和非空 cover_letter_path。
job_fingerprint 绑定数据库当前URL与JD，变化后须重新核实而非只补写哈希。
自动验证只检查记录和证据完整性，无法替代Agent的语义判断。

禁止代点最终提交。适配器完成不等于投递成功。
只凭真实回执或本人明确确认成功登记主库；本地浏览器进度由本人导出后核对。
"""
    write_private(folder / "AGENT_TASK.md", text)
    return folder / "AGENT_TASK.md"


def main() -> int:
    parser = argparse.ArgumentParser(description="只编辑easy_settings.json，先检查和预览，再显式运行")
    parser.add_argument("action", choices=("init", "check", "plan", "run"))
    parser.add_argument("--settings", type=Path, default=EASY_SETTINGS)
    parser.add_argument("--base-config", type=Path, default=PRIVATE_CONFIG if PRIVATE_CONFIG.is_file() else PROJECT_ROOT / "job_bot/config.china_hk_ic_foreign.json")
    parser.add_argument("--task", choices=TASKS, default="assist")
    parser.add_argument("--application-id", action="append", type=int, default=[])
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--execute", action="store_true", help="显式允许执行计划；不会最终提交")
    args = parser.parse_args()
    try:
        if any(appid < 1 for appid in args.application_id):
            raise ValueError("投递编号必须为正整数")
        if args.action == "init":
            if args.settings.exists():
                print("配置已存在，保留原文件：", args.settings)
            else:
                write_private(args.settings, (PROJECT_ROOT / "examples/easy_settings_template.json").read_text(encoding="utf-8"))
                print("配置已创建：", args.settings)
            return 0
        data = load_settings(args.settings)
        config = prepare_config(args.settings, args.base_config)
        if args.action == "check":
            print("配置检查通过；最终提交由本人完成")
            return 0
        if args.action == "plan":
            print(json.dumps({"模式": data["01_使用模式"]["模式"], "生效控制": config["operator_controls"],
                "开启采集源数": sum(source.get("enabled", True) for source in config.get("sources", [])),
                "说明": "plan只读配置；run默认仍预览，增加--execute才执行。assist/materials生成Agent任务包。"}, ensure_ascii=False, indent=2))
            return 0
        require_module(config, args.task)
        command = task_command(args.task, args.application_id, args.manifest, args.output)
        if args.task == "fill" and config["operator_controls"]["mode"] == "manual":
            raise ValueError("手动辅助模式请使用 --task assist；自动模式还需开启自动填写开关")
        if not args.execute:
            print(json.dumps({"task": args.task, "command": command, "执行": False, "说明": "增加--execute执行；无command表示生成Agent任务包"}, ensure_ascii=False, indent=2))
            return 0
        write_private(EASY_RUNTIME_CONFIG, json.dumps(config, ensure_ascii=False, indent=2) + "\n")
        from job_bot.operator_browser import browser_transport
        from job_bot.source_selector import uses_browser
        needed = args.task in ("fill", "sessions") or (args.task == "assist" and config["operator_controls"]["capture_assist"])
        needed = needed or (args.task == "scan" and any(source.get("enabled", True) and uses_browser(source) for source in config.get("sources", [])))
        if needed and args.task != "scan" and config["operator_controls"]["browser_transport"] == "builtin":
            raise ValueError("填写、登录检查和截图需使用已登录的专用CDP浏览器；临时内置浏览器用于采集")
        if needed:
            load_env_file(CREDENTIALS_FILE)
        with browser_transport(config, needed):
            if command is None:
                print("交给Agent读取：", agent_packet(config, args.task, args.application_id))
                return 0
            if args.task == "fill":
                command.append("--execute")
            return subprocess.run(command, cwd=PROJECT_ROOT, check=False).returncode
    except (ValueError, OSError) as exc:
        # 报告控制项错误；绝不转储运行时配置或候选人资料值。
        print(f"无法运行：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

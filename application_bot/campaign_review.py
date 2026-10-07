#!/usr/bin/env python3
"""为禁止提交申请批次生成供人工审阅/操作的队列。"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from job_bot.bot import connect_db, load_config  # noqa: E402


DEFAULT_CONFIG = ROOT / "job_bot/config.china_hk_ic_foreign.json"
from private_paths import APPLICATION_OUTPUT

DEFAULT_OUT = APPLICATION_OUTPUT


ACTION = {
    "qr_login_policy_required": (
        "扫描 Hotjob 微信二维码，并决定是否接受显示的隐私政策；"
        "仅此操作即可解锁 Horizon/UNISOC 的全部 40 个岗位。"
    ),
    "authentication_required": (
        "请在专用 Chrome 中完成相应门户登录/账户步骤。Workday 租户、"
        "Apple 和 Moore Threads 使用独立会话。"
    ),
    "browser_form_started": (
        "审阅已打开的表单。Moka 仍需出生日期和国籍；Infineon 需要"
        "居住国家和期望入职日期。暂时不要点击最终提交按钮。"
    ),
    "profile_ready_final_only": (
        "审阅 MediaTek 全局档案。该网站没有按岗位区分的草稿：下一步岗位操作将是"
        "最终提交，因此定制 PDF 仍保留在本地审阅材料包中。"
    ),
    "captcha_required": "完成每个 AMD hCaptcha；邮箱和 AMD 隐私同意已填写。",
    "policy_consent_required": (
        "自动化继续前，请审阅 TI 政策/同意页面并明确选择授权或拒绝。"
    ),
    "account_creation_required": (
        "创建 Qualcomm 候选人账户或使用 Google 登录；已填写获授权的邮箱。"
    ),
    "external_redirect_unresolved": (
        "请手动打开雇主的实际目标网站。JobsDB 仅记录了外部网站访问，"
        "并未提交申请。"
    ),
    "job_detail_unresolved": (
        "修复当前 Alibaba 职位详情网址映射；已存储的链接目前会打开通用职位列表。"
    ),
    "profile_repair_required": (
        "修复或重新创建实习僧在线简历；当前完善资料链接会跳转到 /resume/undefined。"
    ),
    "portal_unreachable": "修复昆仑芯科技职位详情路由；该路由当前打开空白页面。",
    "draft_saved": "审阅已保存的 NVIDIA 服务器草稿；尚未最终提交。",
}

PRIORITY = {
    "qr_login_policy_required": 1,
    "authentication_required": 2,
    "browser_form_started": 3,
    "profile_ready_final_only": 4,
    "captcha_required": 5,
    "policy_consent_required": 6,
    "account_creation_required": 7,
    "draft_saved": 8,
    "external_redirect_unresolved": 9,
    "job_detail_unresolved": 10,
    "profile_repair_required": 11,
    "portal_unreachable": 12,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-id", type=int, default=2)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = parser.parse_args()
    conn = connect_db(load_config(Path(args.config)))
    rows = conn.execute(
        """
        SELECT acj.rank, acj.status, acj.application_id, jobs.company,
               jobs.title, jobs.location, applications.draft_url
        FROM application_campaign_jobs acj
        JOIN jobs ON jobs.id=acj.job_id
        LEFT JOIN applications ON applications.id=acj.application_id
        WHERE acj.campaign_id=? ORDER BY acj.rank
        """,
        (args.campaign_id,),
    ).fetchall()
    groups = defaultdict(list)
    for row in rows:
        groups[row["status"]].append(row)
    lines = [
        f"# 批次 {args.campaign_id} 审阅操作",
        "",
        f"生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        "",
        f"岗位总数：{len(rows)}。最终提交数：0。",
        "",
        "每个岗位均有定制简历和求职信 PDF。浏览器/服务器进度见下方。",
        "",
    ]
    for status in sorted(groups, key=lambda value: PRIORITY.get(value, 99)):
        items = groups[status]
        lines.extend(
            [
                f"## {status} ({len(items)})",
                "",
                ACTION.get(status, "请人工审阅此状态。"),
                "",
                "| 序号 | 申请 | 公司 | 职位 | 地点 |",
                "|---:|---:|---|---|---|",
            ]
        )
        for row in items:
            lines.append(
                f"| {row['rank']} | {row['application_id']} | {row['company']} | "
                f"{row['title']} | {row['location']} |"
            )
        lines.append("")
    output = DEFAULT_OUT / f"campaign_{args.campaign_id:03d}_review_actions_{datetime.now():%Y%m%d_%H%M%S}.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()

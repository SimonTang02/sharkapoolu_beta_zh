"""将宾大以收入为导向的线索与企业职业评分分开处理。"""

import json
import math
from pathlib import Path


def numeric_range(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float))
           or not math.isfinite(v) or v < 0 for v in value):
        return None
    return list(value) if value[0] <= value[1] else None


def estimate_income(hourly, weekly):
    """按平均自然月计算；不虚构工资，也不保证班次。"""
    wage, hours = numeric_range(hourly), numeric_range(weekly)
    monthly = [round(v * 52 / 12, 1) for v in hours] if hours else None
    gross = ([round(wage[i] * hours[i] * 52 / 12, 2) for i in (0, 1)]
             if wage and hours else None)
    return {"monthly_hours": monthly, "monthly_gross_usd": gross}


def display_range(value, money=False):
    if value is None:
        return "待确认" if not money else "无法估算（缺时薪或工时）"
    parts = [f"{v:,.2f}" if money else f"{v:g}" for v in value]
    return ("$" if money else "") + (parts[0] if value[0] == value[1] else "–".join(parts))


def cell(value):
    return str(value).replace("|", "／").replace("\n", " ")


def render_campus_employment(config: dict, output_dir: Path):
    settings = config.get("penn_channels", {}).get("campus_employment", {})
    if not settings.get("enabled", False):
        return [], None
    snapshot = {}
    try:
        snapshot = json.loads((output_dir / "penn_channels/latest.json").read_text())
    except (OSError, ValueError):
        pass
    workday = next((x for x in snapshot.get("channels", []) if x.get("id") == "workday"), {})
    refresh = {}
    try:
        refresh = json.loads((output_dir / "penn_channels/campus_refresh.json").read_text())
    except (OSError, ValueError):
        pass
    rows = [dict(x) for x in settings.get("leads", [])]
    rows += [dict(x) for x in snapshot.get("opportunities", []) if x.get("kind") == "campus_job"]
    rows.sort(key=lambda x: x.get("priority", 50))
    lines = ["## 校内工作／实习：收入与 SSN 优先", "",
             "目标：尽快获得真实有薪雇佣；优先核验近期能入职、资格符合、报酬明确且手续齐全的岗位，专业对口作为加分项。图书馆、行政、场馆、IT 支持、助教和有薪科研均可考虑。",
             "专业 IC 评分不用于筛掉本板块岗位；以下是核验队列，不代表已确认可入职。", "",
             f"Workday 最近状态：`{workday.get('status', '尚未读取')}`；核验时间：{workday.get('checked_at', snapshot.get('checked_at', '未知'))}。",
             "[学生岗位入口](https://srfs.upenn.edu/student-employment/job-search)；登录页已保留，登录后需读取岗位详情补齐当前薪资、工时和资格。", "",
             "### 岗位与待遇核验队列", "",
             "| 岗位 / 来源 | 招聘状态；核验日期 | 时薪（USD） | 周工时 → 估计月工时 | 估计税前月收入（USD） | 资格与下一步 |",
             "|---|---|---|---|---|---|"]
    for row in rows:
        row.update(estimate_income(row.get("hourly_usd"), row.get("weekly_hours")))
        wage = numeric_range(row.get("hourly_usd"))
        wage_text = display_range(wage, True) if wage else "未公布／待确认"
        columns = [f"[{cell(row['title'])}]({row['url']})",
                   f"{row.get('status', '仅列表线索，详情待核验')}；{row.get('checked_at', snapshot.get('checked_at', '未知'))}",
                   wage_text + ("；" + row['pay_basis'] if row.get('pay_basis') else ""),
                   f"{display_range(numeric_range(row.get('weekly_hours')))} → {display_range(row['monthly_hours'])}；{row.get('hours_basis', '排班待确认')}",
                   display_range(row['monthly_gross_usd'], True),
                   f"{row.get('eligibility', '资格待确认')}；{row.get('next_step', '核实在招、有薪、最早入职和雇主证明')}"]
        lines.append("| " + " | ".join(cell(x) for x in columns) + " |")
    if not rows:
        lines.append("| 暂无已采集校内岗位 | 等待登录或详情读取 | 待确认 | 待确认 | 无法估算 | 优先读取 Workday Student Employment |")
    if refresh:
        lines += ["", "### 各网站本轮校内兼职核验", "",
                  f"核验时间：{refresh.get('checked_at', '未知')}。这是逐站核验记录，不代表全量岗位覆盖。", "",
                  "| 网站 | 本轮结果与覆盖范围 |", "|---|---|"]
        for source in refresh.get("coverage", []):
            lines.append(f"| {cell(source['name'])} | {cell(source['result'])} |")
        lines += ["", "[本轮详细记录及排除项](penn_channels/campus_refresh.md)", ""]
    lines += ["", "月工时 = 周工时 × 52 ÷ 12；税前月收入 = 时薪 × 周工时 × 52 ÷ 12。按完整平均月份估算，首月按实际入职日、排班及无薪休息调整；不代表到手工资。历史报价仅作参考，缺失值不按零处理。",
              "CURF 未确认报酬的科研、活动和校友资源不计入收入岗位；Work-Study 资格未知的岗位不认定为可申请。", "",
              "### 收入预算情景（假设，非岗位报价）", "",
              "| 假设时薪 | 每周 10 小时／月约 43.3 小时 | 每周 15 小时／月 65 小时 | 每周 20 小时／月约 86.7 小时 |",
              "|---|---|---|---|"]
    for rate in settings.get("scenario_hourly_usd", [15, 18, 20]):
        values = [estimate_income([rate, rate], [hours, hours])["monthly_gross_usd"] for hours in (10, 15, 20)]
        lines.append(f"| ${rate:g} | " + " | ".join(display_range(v, True) for v in values) + " |")
    lines += ["", "### 雇佣与 SSN 下一步", "",
              "先确认有薪 offer、雇主及最早开始日期，再让雇佣部门完成校内工作证明，通过 iPenn 申请 ISSS SSN 支持信，按 SSA 流程预约并提交材料；不能仅凭岗位链接保证获得 SSN。[Penn 官方 SSN 流程](https://global.upenn.edu/isss/ssn/)。",
              "若为 F-1/J-1，上课期间所有校内工作合计通常每周不超过 20 小时；86.7 小时只是月均换算，不能跨周调剂额度。J-1 开工前还需 ISSS 书面许可。CHOP、Wistar 等不能仅因与 Penn 有关联就视为校内工作；身份及雇主资格需按 [ISSS 校内工作规则](https://global.upenn.edu/isss/oncampus/) 核验。", ""]
    return lines, {"priority": "paid employment and near-term start; major fit secondary",
                   "workday": workday, "refresh": refresh, "leads": rows, "weeks_per_month": 52 / 12,
                   "policy_checked_at": "2026-09-18"}

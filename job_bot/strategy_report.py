#!/usr/bin/env python3
"""生成 2027 年校园招聘、美国应届生职位和美国实习职位审阅队列。"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_CONFIG = ROOT / "job_bot/config.china_hk_ic_foreign.json"

from job_bot.bot import db_path, load_config
from job_bot.shared_database import connect as connect_database
from private_paths import JOB_DATABASE, JOBBOT_OUTPUT

DEFAULT_DB = JOB_DATABASE
DEFAULT_OUT = JOBBOT_OUTPUT

FOUNDATIONS: list[tuple[str, int, tuple[str, ...]]] = [
    (
        "architecture",
        96,
        (
            "computer architecture", "microarchitecture", "gpu architecture",
            "cpu architecture", "soc architecture", "processor architecture",
            "performance modeling", "架构", "微架构", "处理器设计",
        ),
    ),
    (
        "digital_design",
        92,
        (
            "digital design", "digital ic", "asic design", "rtl design",
            "logic design", "soc front", "front-end design", "frontend design",
            "数字芯片设计", "数字ic设计", "soc前端", "soc开发", "前端设计",
            "芯片设计工程师", "rtl", "asic",
        ),
    ),
    (
        "accelerator_design",
        90,
        ("npu", "bpu", "ai accelerator", "tensor processor", "神经网络处理器", "加速器设计"),
    ),
    (
        "fpga_design",
        88,
        ("fpga", "programmable logic", "可编程逻辑"),
    ),
    (
        "verification",
        54,
        (
            "design verification", "hardware verification", "rtl verification",
            "formal verification", "pre-silicon verification", "uvm", "验证工程师",
            "芯片验证", "逻辑验证", "原型验证", "verification",
        ),
    ),
    (
        "synthesis_sta",
        79,
        (
            "logic synthesis", "synthesis", "static timing", "sta", "timing closure",
            "lint", "clock domain crossing", "cdc", "逻辑综合", "时序", "芯片中端",
        ),
    ),
    (
        "physical_design",
        73,
        (
            "physical design", "place and route", "place & route", "p&r",
            "backend", "back-end", "物理设计", "数字后端", "芯片后端",
        ),
    ),
    (
        "dft",
        70,
        ("design for test", "testability", "scan insertion", "dft", "可测性"),
    ),
    (
        "eda",
        68,
        ("electronic design automation", "eda", "cad engineer", "circuit simulation"),
    ),
    (
        "analog_mixed_signal",
        58,
        ("analog design", "mixed signal", "mixed-signal", "模拟芯片", "模拟ic", "定制电路"),
    ),
    (
        "hardware_validation",
        60,
        ("hardware", "silicon validation", "post-silicon", "fpga", "circuit design"),
    ),
]

CHINA_HK = re.compile(
    r"china|hong kong|中国|香港|北京|上海|深圳|苏州|杭州|南京|成都|西安|武汉|合肥|天津|广州|东莞|厦门|珠海|无锡",
    re.I,
)
UNITED_STATES = re.compile(
    r"united states|\bus\b|u\.s\.|california|texas|massachusetts|colorado|oregon|washington|"
    r"arizona|new york|new jersey|pennsylvania|north carolina|vermont|maine|santa clara|san jose|"
    r"austin|hillsboro|boxborough|fort collins|longmont|rochester|secaucus|irvine|"
    r"chicago|needham|cambridge|\b(?:IL|MA|NY|CA|PA|TX|WA|OR|CO|AZ|NJ|NC|MD)\b",
    re.I,
)
INTERNSHIP = re.compile(r"\bintern(?:ship)?s?\b|\bco-?op\b|实习|實習", re.I)
EXPLICIT_2027 = re.compile(r"2027|27届|2027届|2027 届", re.I)
CAMPUS = re.compile(r"new college grad|new grad|graduate|campus|应届|應屆|校招|校园招聘|校园", re.I)
SUMMER = re.compile(
    r"summer(?:/fall)?\s*2027|2027\s*summer|2027 internships?|"
    r"from\s+(?:may|june|july)\b.{0,55}?2027\s+to\s+(?:august|september)",
    re.I | re.S,
)
SPRING_WINTER_ONLY = re.compile(r"winter/spring 2027|spring 2027", re.I)
SENIOR = re.compile(
    r"\b(?:senior|staff|principal|manager|director|lead)\b|资深|高级|主管|经理|负责人|专家",
    re.I,
)
OFF_DIRECTION = re.compile(
    r"sales|marketing|account manager|recruit|finance|software test|data analyst|"
    r"^software engineering|developer technology|autonomous vehicles and robotics|"
    r"solution architect|解决方案架构|编译|compiler|芯片软件|开源框架|"
    r"mechanical|thermal|packag(?:e|ing)|product development|fae|现场应用|销售|市场|财务|封装工艺|自动驾驶系统架构|软件系统|基础架构平台",
    re.I,
)
MINIMUM_SCORE = 58
TIER_A_SCORE = 85
TIER_B_SCORE = 68
CLASSIFICATION_PRIORITY = [
    "architecture", "digital_design", "accelerator_design", "fpga_design",
    "synthesis_sta", "physical_design", "dft", "eda",
    "analog_mixed_signal", "hardware_validation", "verification",
]
TRACKS = {
    "cn_hk_2027_campus": {"enabled": True, "role_kinds": ["full_time", "unknown"]},
    "us_2027_new_grad": {"enabled": True, "role_kinds": ["full_time", "unknown"]},
    "us_summer_2027_internship": {"enabled": True, "role_kinds": ["internship"]},
}
EXCLUDE_VERIFICATION_ONLY = True
VERIFICATION_TITLE = re.compile(r"verification|validation|验证|驗證|测试|\buvm\b|\bDV intern\b", re.I)
STORED_SCORE_BONUS = {"minimum": 70, "points": 2}
STRATEGY_SCORE_MODIFIERS = [
    {"points": 4, "scope": "title", "pattern": r"architecture|microarchitecture|架构|处理器"},
    {"points": 3, "scope": "title", "pattern": r"rtl|digital|asic|soc前端|数字芯片"},
    {"points": 2, "scope": "all", "pattern": r"systemverilog|verilog|cocotb|risc-v|risc v|cpu|gpu"},
]
FOUNDATION_OVERRIDES = [
    {"title_pattern": r"Hardware(?: Technologies)? Masters Engineering Internships", "foundation": "digital_design", "base_score": 86},
    {"title_pattern": r"2027 Masters Hardware Engineering (?:intern|co-op)", "foundation": "digital_design", "base_score": 84},
    {"title_pattern": r"NVIDIA 2027 Internships: Hardware Engineering", "foundation": "digital_design", "base_score": 86},
    {"title_pattern": r"^DV Intern\b", "foundation": "verification", "base_score": 54},
    {"title_pattern": r"^PD Intern\b", "foundation": "physical_design", "base_score": 73},
]


def configure_strategy(config: dict) -> None:
    """应用策略配置，同时保留旧版配置的默认值。"""
    global FOUNDATIONS, CHINA_HK, UNITED_STATES, INTERNSHIP, EXPLICIT_2027
    global CAMPUS, SUMMER, SPRING_WINTER_ONLY, SENIOR, OFF_DIRECTION
    global MINIMUM_SCORE, TIER_A_SCORE, TIER_B_SCORE, CLASSIFICATION_PRIORITY, TRACKS
    global STORED_SCORE_BONUS, STRATEGY_SCORE_MODIFIERS, FOUNDATION_OVERRIDES
    global EXCLUDE_VERIFICATION_ONLY
    strategy = config.get("strategy", {})
    foundations = strategy.get("foundations")
    if foundations:
        FOUNDATIONS = [
            (
                str(item["name"]),
                int(item["base_score"]),
                tuple(str(keyword) for keyword in item.get("keywords", [])),
            )
            for item in foundations
        ]
    patterns = strategy.get("patterns", {})
    targets = {
        "china_hk": "CHINA_HK",
        "united_states": "UNITED_STATES",
        "internship": "INTERNSHIP",
        "explicit_2027": "EXPLICIT_2027",
        "campus": "CAMPUS",
        "summer": "SUMMER",
        "spring_winter_only": "SPRING_WINTER_ONLY",
        "senior": "SENIOR",
        "off_direction": "OFF_DIRECTION",
    }
    namespace = globals()
    for key, target in targets.items():
        if key in patterns:
            namespace[target] = re.compile(str(patterns[key]), re.I)
    MINIMUM_SCORE = int(strategy.get("minimum_score", MINIMUM_SCORE))
    thresholds = strategy.get("tier_thresholds", {})
    TIER_A_SCORE = int(thresholds.get("A", TIER_A_SCORE))
    TIER_B_SCORE = int(thresholds.get("B", TIER_B_SCORE))
    CLASSIFICATION_PRIORITY = [
        str(item) for item in strategy.get(
            "classification_priority", CLASSIFICATION_PRIORITY
        )
    ]
    TRACKS = dict(strategy.get("tracks", TRACKS))
    EXCLUDE_VERIFICATION_ONLY = bool(strategy.get("exclude_verification_only", True))
    STORED_SCORE_BONUS = dict(strategy.get("stored_score_bonus", STORED_SCORE_BONUS))
    STRATEGY_SCORE_MODIFIERS = list(
        strategy.get("score_modifiers", STRATEGY_SCORE_MODIFIERS)
    )
    FOUNDATION_OVERRIDES = list(
        strategy.get("foundation_overrides", FOUNDATION_OVERRIDES)
    )


@dataclass
class Candidate:
    job_id: int
    track: str
    company: str
    title: str
    location: str
    score: int
    tier: str
    foundation: str
    eligibility: str
    source: str
    url: str
    first_seen: str


def matches(text: str, terms: tuple[str, ...]) -> bool:
    folded = text.casefold()
    return any(term.casefold() in folded for term in terms)


def foundation_for(text: str) -> tuple[str, int] | None:
    by_name = {name: (score, terms) for name, score, terms in FOUNDATIONS}
    for name in CLASSIFICATION_PRIORITY:
        item = by_name.get(name)
        if item and matches(text, item[1]):
            return name, item[0]
    return None


def campus_signal(title: str, text: str, source: str, url: str) -> tuple[bool, str]:
    if EXPLICIT_2027.search(title) or (
        EXPLICIT_2027.search(text) and CAMPUS.search(title + " " + source)
    ):
        return True, "明确 2027/27 届"
    if CAMPUS.search(title):
        return True, "校招/应届入口；需确认毕业窗口"
    if re.search(r"campus|校招|校园|toptalent", source + " " + url, re.I):
        return True, "官方校招入口；需确认毕业窗口"
    return False, ""


def internship_eligibility(title: str, text: str, source: str) -> tuple[bool, str]:
    combined = f"{title} {text} {source}"
    if re.search(
        r"must be a \(i\) u\.s\. citizen|u\.s\. citizen or national|lawful permanent resident|itar requirements",
        combined,
        re.I,
    ):
        return False, "要求美国公民/永久居民等 ITAR 身份"
    if re.search(r"ph\.?d\.?\s*(?:only|required)|pursuing a ph\.?d", combined, re.I) and not re.search(
        r"b\.?s\.?|m\.?s\.?|bachelor|master", combined, re.I
    ):
        return False, "仅限 PhD"
    if re.search(r"undergrad", title, re.I):
        return False, "职位标题明确仅限本科生"
    if re.search(r"ph\.?d", title, re.I):
        return False, "职位标题明确仅限 PhD"
    if re.search(r"bachelor'?s program", combined, re.I) and not re.search(
        r"master|m\.?s\.?|graduate student", combined, re.I
    ):
        return False, "仅限本科生"
    has_summer = bool(SUMMER.search(combined))
    if "Apple United States Silicon Internships" in source and re.search(
        r"Hardware(?: Technologies)? Masters Engineering Internships", title, re.I
    ):
        return True, "Apple 硬件硕士滚动实习；需在匹配团队时确认 Summer 2027 日期"
    if SPRING_WINTER_ONLY.search(title) and not re.search(r"summer", title, re.I):
        return False, "仅 Winter/Spring 2027"
    if not has_summer:
        return False, "未确认 Summer 2027"
    note = "岗位标示 2027 实习；需逐岗核对日期、2027-06 毕业与 CPT/OPT 资格"
    if re.search(r"not eligible for visa sponsorship|no visa sponsorship", text, re.I):
        note += "；不提供 sponsorship，需以 CPT/现有授权申请"
    elif re.search(r"must be legally authorized|work authorization", text, re.I):
        note += "；需核对美国工作授权问法"
    return True, note


def strategy_score(base: int, stored_score: int | None, title: str, text: str) -> int:
    score = base
    combined = f"{title} {text}"
    for modifier in STRATEGY_SCORE_MODIFIERS:
        haystack = title if modifier.get("scope", "all") == "title" else combined
        if re.search(str(modifier.get("pattern", "")), haystack, re.I):
            score += int(modifier.get("points", 0))
    if stored_score is not None and stored_score >= int(
        STORED_SCORE_BONUS.get("minimum", 70)
    ):
        score += int(STORED_SCORE_BONUS.get("points", 2))
    return min(score, 100)


def foundation_override(title: str) -> tuple[str, int] | None:
    for override in FOUNDATION_OVERRIDES:
        if re.search(str(override.get("title_pattern", "")), title, re.I):
            return str(override["foundation"]), int(override["base_score"])
    return None


def is_verification_only(title: str) -> bool:
    """对职位名称含 verification 的岗位采取保守处理，等待人工明确覆盖。"""
    return EXCLUDE_VERIFICATION_ONLY and bool(VERIFICATION_TITLE.search(title))


def us_new_grad_signal(title: str, text: str, source: str) -> tuple[bool, str]:
    if INTERNSHIP.search(title) or re.search(r"ph\.?d\.? only|博士专属", title, re.I):
        return False, ""
    if re.search(r"2026|2028|2029", title) and not EXPLICIT_2027.search(title):
        return False, ""
    if EXPLICIT_2027.search(title) or CAMPUS.search(title) or (
        EXPLICIT_2027.search(text) and CAMPUS.search(source)
    ):
        return True, "需核对 2027-06 毕业窗口、美国工作授权及后续雇主支持"
    return False, ""


def hard_limit_capacity(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """返回当前招聘窗口内雇主/类别剩余的申请名额。"""
    today = dt.date.today().isoformat()
    result: dict[str, dict[str, int]] = {}
    for limit in conn.execute(
        """SELECT company, category, max_applications, window_start, window_end
           FROM application_limits
           WHERE enforcement='hard' AND window_start<=? AND window_end>=?""",
        (today, today),
    ):
        category = str(limit["category"])
        count = conn.execute(
            """SELECT count(DISTINCT a.id)
               FROM applications a JOIN jobs j ON j.id=a.job_id
               WHERE lower(j.company)=lower(?) AND a.status='submitted'
                 AND (?='all' OR j.recruitment_category=?)
                 AND substr(coalesce(a.submitted_at,a.created_at),1,10)
                     BETWEEN ? AND ?""",
            (limit["company"], category, category,
             limit["window_start"], limit["window_end"]),
        ).fetchone()[0]
        remaining = max(0, int(limit["max_applications"]) - int(count))
        result.setdefault(str(limit["company"]).casefold(), {})[category] = remaining
    return result


def exhausted_hard_limits(conn: sqlite3.Connection) -> dict[str, set[str]]:
    return {
        company: {category for category, remaining in categories.items() if remaining == 0}
        for company, categories in hard_limit_capacity(conn).items()
    }


def hard_limit_holds_job(
    company: str, category: str | None, role_kind: str, limits: dict[str, set[str]]
) -> bool:
    company_limits = limits.get(company.casefold(), set())
    return bool(company_limits) and (
        "all" in company_limits or category in company_limits or
        (not category and role_kind in ("full_time", "unknown")
         and any(key.endswith("_campus") for key in company_limits))
    )


def tier_for(score: int) -> str:
    if score >= TIER_A_SCORE:
        return "A"
    if score >= TIER_B_SCORE:
        return "B"
    return "C"


def collect(conn: sqlite3.Connection) -> tuple[list[Candidate], list[Candidate], list[Candidate], dict[str, int]]:
    conn.row_factory = sqlite3.Row
    blocked_job_ids = {
        int(row[0]) for row in conn.execute(
            """SELECT DISTINCT job_id FROM applications
               WHERE status IN ('submitted', 'eligibility_blocked', 'listing_closed')"""
        )
    }
    capacities = hard_limit_capacity(conn)
    exhausted_limits = {
        company: {category for category, remaining in rules.items() if remaining == 0}
        for company, rules in capacities.items()
    }
    rows = conn.execute(
        """
        SELECT id, company, title, location, url, role_kind, recruitment_category,
               description, status,
               fit_score, source_name, first_seen
        FROM jobs
        WHERE is_active = 1
        """
    ).fetchall()
    campus_jobs: list[Candidate] = []
    us_new_grads: list[Candidate] = []
    us_internships: list[Candidate] = []
    job_categories = {int(row["id"]): str(row["recruitment_category"] or "") for row in rows}
    stats = {
        "active_examined": len(rows),
        "campus_off_direction": 0,
        "campus_not_2027": 0,
        "us_wrong_term_or_degree": 0,
        "below_threshold": 0,
        "verification_held": 0,
        "already_resolved": 0,
        "company_limit_held": 0,
        "remaining_slots_held": 0,
    }

    for row in rows:
        if int(row["id"]) in blocked_job_ids or row["status"] in (
            "applied", "closed", "eligibility_blocked", "listing_closed"
        ):
            stats["already_resolved"] += 1
            continue
        title = str(row["title"] or "")
        if hard_limit_holds_job(
            str(row["company"] or ""), row["recruitment_category"],
            str(row["role_kind"] or ""), exhausted_limits
        ):
            stats["company_limit_held"] += 1
            continue
        if is_verification_only(title):
            stats["verification_held"] += 1
            continue
        location = str(row["location"] or "")
        source = str(row["source_name"] or "")
        description = str(row["description"] or "")
        url = str(row["url"] or "")
        combined = " ".join((title, location, description, source, url))
        foundation = foundation_override(title) or foundation_for(title)
        if not foundation and re.search(r"hardware|silicon", title, re.I):
            foundation = ("hardware_validation", 60)
        if not foundation:
            continue
        foundation_name, base = foundation
        score = strategy_score(base, row["fit_score"], title, combined)
        if score < MINIMUM_SCORE:
            stats["below_threshold"] += 1
            continue
        if SENIOR.search(title) or OFF_DIRECTION.search(title):
            stats["campus_off_direction"] += 1
            continue

        geography = location if location.strip() else source
        is_cn_hk = bool(CHINA_HK.search(geography))
        campus_track = TRACKS.get("cn_hk_2027_campus", {})
        if (
            campus_track.get("enabled", True)
            and is_cn_hk
            and row["role_kind"] in campus_track.get("role_kinds", ("full_time", "unknown"))
        ):
            is_campus, eligibility = campus_signal(title, combined, source, url)
            degree_excluded = bool(re.search(r"应届博士|博士专属|ph\.?d\.? only", title, re.I))
            if is_campus and not degree_excluded:
                campus_jobs.append(
                    Candidate(
                        job_id=int(row["id"]),
                        track="cn_hk_2027_campus",
                        company=str(row["company"] or ""),
                        title=title,
                        location=location,
                        score=score,
                        tier=tier_for(score),
                        foundation=foundation_name,
                        eligibility=eligibility,
                        source=source,
                        url=url,
                        first_seen=str(row["first_seen"] or ""),
                    )
                )
            else:
                stats["campus_not_2027"] += 1

        is_us = bool(UNITED_STATES.search(geography)) and not bool(CHINA_HK.search(geography))
        grad_track = TRACKS.get("us_2027_new_grad", {})
        if (
            grad_track.get("enabled", False)
            and is_us
            and row["role_kind"] in grad_track.get("role_kinds", ("full_time", "unknown"))
        ):
            eligible, eligibility = us_new_grad_signal(title, combined, source)
            if eligible:
                us_new_grads.append(
                    Candidate(
                        job_id=int(row["id"]),
                        track="us_2027_new_grad",
                        company=str(row["company"] or ""),
                        title=title,
                        location=location,
                        score=score,
                        tier=tier_for(score),
                        foundation=foundation_name,
                        eligibility=eligibility,
                        source=source,
                        url=url,
                        first_seen=str(row["first_seen"] or ""),
                    )
                )
        us_track = TRACKS.get("us_summer_2027_internship", {})
        if (
            us_track.get("enabled", True)
            and is_us
            and (
                row["role_kind"] in us_track.get("role_kinds", ("internship",))
                or INTERNSHIP.search(title)
            )
        ):
            eligible, eligibility = internship_eligibility(title, combined, source)
            if eligible:
                us_internships.append(
                    Candidate(
                        job_id=int(row["id"]),
                        track="us_summer_2027_internship",
                        company=str(row["company"] or ""),
                        title=title,
                        location=location,
                        score=score,
                        tier=tier_for(score),
                        foundation=foundation_name,
                        eligibility=eligibility,
                        source=source,
                        url=url,
                        first_seen=str(row["first_seen"] or ""),
                    )
                )
            else:
                stats["us_wrong_term_or_degree"] += 1

    def unique_sorted(items: list[Candidate]) -> list[Candidate]:
        deduped: dict[str, Candidate] = {}
        for item in items:
            key = item.url or re.sub(
                r"[^a-z0-9]+", "", f"{item.company} {item.title} {item.location}".casefold()
            )
            previous = deduped.get(key)
            if previous is None or item.score > previous.score:
                deduped[key] = item
        return sorted(
            deduped.values(),
            key=lambda item: (-item.score, item.company.casefold(), item.title.casefold()),
        )

    used_slots: dict[tuple[str, str], int] = {}

    def keep_within_remaining_slots(items: list[Candidate]) -> list[Candidate]:
        kept = []
        for item in unique_sorted(items):
            company = item.company.casefold()
            category = job_categories[item.job_id]
            applicable = [
                (company, key, remaining)
                for key, remaining in capacities.get(company, {}).items()
                if key == "all" or key == category or
                (not category and item.track == "cn_hk_2027_campus"
                 and key.endswith("_campus"))
            ]
            if any(used_slots.get((co, key), 0) >= remaining
                   for co, key, remaining in applicable):
                stats["remaining_slots_held"] += 1
                continue
            kept.append(item)
            for co, key, _ in applicable:
                used_slots[(co, key)] = used_slots.get((co, key), 0) + 1
        return kept

    return (
        keep_within_remaining_slots(campus_jobs),
        keep_within_remaining_slots(us_new_grads),
        keep_within_remaining_slots(us_internships),
        stats,
    )


def latest_source_statuses(conn: sqlite3.Connection) -> list[dict[str, object]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT run.source_name, run.status, run.jobs_seen, run.jobs_new,
               run.error, run.finished_at
        FROM scan_runs AS run
        JOIN (
          SELECT source_name, MAX(id) AS id
          FROM scan_runs
          GROUP BY source_name
        ) AS latest ON latest.id = run.id
        ORDER BY run.source_name
        """
    ).fetchall()
    return [
        {
            "source": str(row["source_name"] or ""),
            "status": str(row["status"] or ""),
            "jobs_seen": int(row["jobs_seen"] or 0),
            "jobs_new": int(row["jobs_new"] or 0),
            "error": str(row["error"] or "unknown error"),
            "finished_at": str(row["finished_at"] or ""),
        }
        for row in rows
    ]


def latest_source_failures(conn: sqlite3.Connection) -> list[dict[str, str]]:
    return [
        {
            "source": str(row["source"]),
            "error": str(row["error"]),
            "finished_at": str(row["finished_at"]),
        }
        for row in latest_source_statuses(conn)
        if row["status"] == "error"
    ]


def render_section(title: str, items: list[Candidate]) -> list[str]:
    lines = [f"## {title}", ""]
    if not items:
        return lines + ["本轮没有确认到符合条件的在招岗位。", ""]
    for tier in ("A", "B", "C"):
        tier_items = [item for item in items if item.tier == tier]
        if not tier_items:
            continue
        label = {"A": "优先投递", "B": "值得投递", "C": "方向补充"}[tier]
        lines.extend((f"### Tier {tier} — {label}（{len(tier_items)}）", ""))
        for index, item in enumerate(tier_items, 1):
            lines.extend(
                (
                    f"{index}. **{item.title} — {item.company}**",
                    f"   - 地点：{item.location or '未标注'}",
                    f"   - 策略分：{item.score}/100；方向：`{item.foundation}`",
                    f"   - 资格：{item.eligibility}",
                    f"   - 数据库 ID：{item.job_id}；来源：{item.source}",
                    f"   - [官方职位页面]({item.url})",
                    "",
                )
            )
    return lines


def sync_strategy_reviews(
    conn: sqlite3.Connection, config: dict, candidates: list[Candidate]
) -> str:
    """原子替换审阅队列，不修改职位或申请记录。"""
    policy = json.dumps(config.get("strategy", {}), sort_keys=True, ensure_ascii=False)
    policy_hash = hashlib.sha256(policy.encode("utf-8")).hexdigest()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS job_strategy_reviews (
                 job_id INTEGER NOT NULL REFERENCES jobs(id),
                 track TEXT NOT NULL,
                 policy_hash TEXT NOT NULL,
                 score INTEGER NOT NULL,
                 tier TEXT NOT NULL,
                 foundation TEXT NOT NULL,
                 eligibility_note TEXT NOT NULL,
                 review_state TEXT NOT NULL DEFAULT 'review_required',
                 updated_at TEXT NOT NULL,
                 PRIMARY KEY (job_id, track)
               )"""
        )
        conn.execute("DELETE FROM job_strategy_reviews")
        conn.executemany(
            """INSERT INTO job_strategy_reviews
               (job_id, track, policy_hash, score, tier, foundation,
                eligibility_note, review_state, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'review_required', ?)""",
            [
                (item.job_id, item.track, policy_hash, item.score, item.tier,
                 item.foundation, item.eligibility, now)
                for item in candidates
            ],
        )
    return policy_hash


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--db", type=Path, help="覆盖配置中的数据库路径")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--sync-db", action="store_true",
        help="替换数据库中的审阅队列；不会更改职位或申请状态",
    )
    parser.add_argument(
        "--since",
        help="仅包含在此 ISO 时间戳当天或之后首次发现的职位",
    )
    args = parser.parse_args()
    if args.sync_db and args.since:
        parser.error("--sync-db 要求使用完整快照，不能同时指定 --since")

    config = load_config(args.config)
    configure_strategy(config)

    conn = connect_database(args.db or db_path(config))
    campus_jobs, us_new_grads, us_internships, stats = collect(conn)
    source_statuses = latest_source_statuses(conn)
    source_failures = [row for row in source_statuses if row["status"] == "error"]
    policy_hash = None
    if args.sync_db:
        policy_hash = sync_strategy_reviews(
            conn, config, campus_jobs + us_new_grads + us_internships
        )
    conn.close()

    if args.since:
        cutoff = dt.datetime.fromisoformat(args.since.replace("Z", "+00:00"))
        if cutoff.tzinfo is None:
            cutoff = cutoff.astimezone()

        def discovered_after(item: Candidate) -> bool:
            if not item.first_seen:
                return False
            seen = dt.datetime.fromisoformat(item.first_seen.replace("Z", "+00:00"))
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=dt.timezone.utc)
            return seen >= cutoff

        campus_jobs = [item for item in campus_jobs if discovered_after(item)]
        us_new_grads = [item for item in us_new_grads if discovered_after(item)]
        us_internships = [item for item in us_internships if discovered_after(item)]

    now = dt.datetime.now().astimezone()
    stamp = now.strftime("%Y%m%d_%H%M%S")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    report_kind = "delta" if args.since else "full"
    md_path = args.out_dir / f"application_strategy_2027_{report_kind}_{stamp}.md"
    json_path = args.out_dir / f"application_strategy_2027_{report_kind}_{stamp}.json"

    lines = [
        "# 2027 三轨岗位审查清单" + ("（新增）" if args.since else "（全量）"),
        "",
        f"生成时间：{now.isoformat(timespec='seconds')}",
        "",
        *((f"增量基线：{args.since}", "") if args.since else ()),
        "筛选口径：大陆/香港 2027 届校招正职；美国 2027 应届正职及 2027 暑期实习，均需逐岗核对毕业窗口和工作授权。纯验证标题暂不进入自动清单。",
        "方向优先级：架构 > 数字设计/RTL > NPU/加速器 > FPGA > 综合/STA > PD/DFT > EDA > 模拟/通用硬件。",
        "",
    ]
    lines += render_section("赛道一：大陆/香港 2027 届校招正职", campus_jobs)
    lines += render_section("赛道二：美国 2027 应届正职（资格待核）", us_new_grads)
    lines += render_section("赛道三：美国 2027 Summer 实习（日期/身份待核）", us_internships)
    lines += [
        "## 使用说明",
        "",
        "- 简历统一使用真实 Jun 2027 毕业日期；毕业后的美国实习不得假定能用 CPT，须核对 OPT、实习期和雇主要求。",
        "- `不提供 sponsorship` 不等于一定不能使用 CPT；填写时仍需按岗位原文和个人真实身份回答工作授权问题。",
        "- 宾大有薪兼职由 campus_employment_report 单独核验当前空缺、时薪、每周工时和学生身份；专业匹配只作加分。",
        "- 本清单全部为 review_required；Tier A 优先核资格和定制简历，不代表可直接提交。",
        "",
        "## 扫描统计",
        "",
        f"- 活跃岗位检查：{stats['active_examined']}",
        f"- 大陆/香港入选：{len(campus_jobs)}",
        f"- 美国应届正职待核：{len(us_new_grads)}",
        f"- 美国暑期实习入选：{len(us_internships)}",
        f"- 纯验证标题暂缓：{stats['verification_held']}",
        f"- 已提交／资格不符／关闭而排除：{stats['already_resolved']}",
        f"- 招聘周期硬性名额已满而暂缓：{stats['company_limit_held']}",
        f"- 仅保留剩余志愿数而暂缓：{stats['remaining_slots_held']}",
        f"- 美国因学位或实习学期不符而排除：{stats['us_wrong_term_or_degree']}",
        "",
        "## 数据源覆盖",
        "",
        f"- 最近一次扫描成功：{sum(row['status'] == 'ok' for row in source_statuses)} 个来源",
        f"- 最近一次扫描失败：{len(source_failures)} 个来源",
        "- 正文只显示本次增量中通过地区、届别、学历和方向筛选的职位；来源成功但没有合格新增时不会出现在职位正文。",
        "",
        "<details>",
        "<summary>展开查看成功来源及读取数量</summary>",
        "",
    ]
    for source in source_statuses:
        if source["status"] == "ok":
            lines.append(f"- {source['source']}：读取 {source['jobs_seen']} 条")
    lines += [
        "",
        "</details>",
        "",
        "## 数据源限制",
        "",
        "以下来源的最后一次扫描仍失败，因此‘全量’指所有本轮可正常读取并通过资格筛选的岗位：",
        "",
    ]
    if source_failures:
        for failure in source_failures:
            concise_error = " ".join(failure["error"].split())[:220]
            lines.append(f"- {failure['source']}：{concise_error}")
    else:
        lines.append("- 无扫描错误。")
    lines.extend(
        (
            "- BOSS 直聘三个来源仍按配置禁用，未计入本报告。",
            "",
        )
    )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    payload = {
        "generated_at": now.isoformat(timespec="seconds"),
        "since": args.since,
        "campus_jobs": [asdict(item) for item in campus_jobs],
        "us_new_grads": [asdict(item) for item in us_new_grads],
        "us_summer_internships": [asdict(item) for item in us_internships],
        "policy_hash": policy_hash,
        "stats": stats,
        "source_statuses": source_statuses,
        "source_failures": source_failures,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(md_path)
    print(json_path)
    print(f"campus={len(campus_jobs)} us_new_grad={len(us_new_grads)} us_summer={len(us_internships)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

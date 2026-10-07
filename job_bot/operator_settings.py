"""验证入门模式控制项，并将其转换为现有运行时配置。"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from private_paths import EASY_SETTINGS
from job_bot.source_selector import uses_browser

MODES = {"手动辅助": "manual", "快速填写": "quick", "完整填写": "full"}
GROUPS = {
    "01_使用模式": {"总开关": bool, "模式": str, "自动最终提交": bool},
    "02_功能模块": {k: bool for k in ("job_bot", "application_bot", "扫描职位", "辅助手动填写", "自动填写", "生成简历材料", "生成手动投递包", "生成报告", "检查登录状态")},
    "03_浏览器与采集": {"HTTP采集": bool, "内置浏览器": bool, "CDP浏览器": bool, "浏览器选择": str, "内置浏览器无界面": bool, "同时扫描来源数": int, "网络重试次数": int},
    "04_审查与额度": {"投递前审查轮数": int, "快速模式每岗秒数": int, "完整模式每岗秒数": int, "快速模式重试次数": int, "完整模式重试次数": int, "截取辅助截图": bool},
    "05_地区范围": {k: bool for k in ("美国", "香港", "中国大陆", "新加坡", "欧洲", "其他地区", "保留未知地点")},
}
MODULES = {
    "scan": ("job_bot", "扫描职位"), "assist": ("application_bot", "辅助手动填写"),
    "fill": ("application_bot", "自动填写"), "materials": ("application_bot", "生成简历材料"),
    "manual_kit": ("application_bot", "生成手动投递包"), "report": ("job_bot", "生成报告"),
    "sessions": ("application_bot", "检查登录状态"),
}


def validate_settings(data: dict) -> None:
    if not isinstance(data, dict) or type(data.get("版本")) is not int or data["版本"] != 1:
        raise ValueError("配置版本必须为1")
    for group, fields in GROUPS.items():
        if not isinstance(data.get(group), dict):
            raise ValueError(f"缺少配置区域：{group}")
        for key, kind in fields.items():
            if type(data[group].get(key)) is not kind:
                raise ValueError(f"{group}.{key} 的类型不正确")
        if any(k not in fields and not k.startswith("_") for k in data[group]):
            raise ValueError(f"{group} 含未知开关，请检查拼写")
    if any(k not in GROUPS and k != "版本" and not k.startswith("_") for k in data):
        raise ValueError("配置包含未知区域，请检查拼写")
    mode = data["01_使用模式"]
    if mode["模式"] not in MODES:
        raise ValueError("模式必须是手动辅助、快速填写或完整填写")
    if mode["自动最终提交"]:
        raise ValueError("自动最终提交必须为false；本工具只自动准备，最终提交由本人完成")
    browser = data["03_浏览器与采集"]
    if browser["浏览器选择"] not in ("内置", "CDP"):
        raise ValueError("浏览器选择必须是内置或CDP")
    if not 1 <= browser["同时扫描来源数"] <= 16 or not 0 <= browser["网络重试次数"] <= 5:
        raise ValueError("同时扫描来源数须为1～16；网络重试次数须为0～5")
    review = data["04_审查与额度"]
    if not 1 <= review["投递前审查轮数"] <= 10:
        raise ValueError("投递前审查轮数须为1～10，每轮均包含三项核对")
    for key in ("快速模式每岗秒数", "完整模式每岗秒数"):
        if not 5 <= review[key] <= 1800:
            raise ValueError(f"{key} 须为5～1800")
    for key in ("快速模式重试次数", "完整模式重试次数"):
        if not 0 <= review[key] <= 3:
            raise ValueError(f"{key} 须为0～3")


def load_settings(path: Path = EASY_SETTINGS) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("无法读取配置；请检查文件路径和JSON逗号、引号") from exc
    validate_settings(data)
    return data


def controls(data: dict) -> dict:
    validate_settings(data)
    mode = data["01_使用模式"]
    budgets = data["04_审查与额度"]
    selected = MODES[mode["模式"]]
    short = selected == "quick"
    return {
        "enabled": mode["总开关"], "mode": selected,
        "modules": {name: data["02_功能模块"][parent] and data["02_功能模块"][feature]
                    for name, (parent, feature) in MODULES.items()},
        "review_rounds": budgets["投递前审查轮数"],
        "job_timeout_seconds": budgets["快速模式每岗秒数" if short else "完整模式每岗秒数"],
        "adapter_retries": budgets["快速模式重试次数" if short else "完整模式重试次数"],
        "on_interruption": "skip" if short else "stop", "capture_assist": budgets["截取辅助截图"],
        "regions": {k: v for k, v in data["05_地区范围"].items() if not k.startswith("_")},
    }


def require_module(config: dict, module: str) -> None:
    policy = config.get("operator_controls")
    if policy and (not policy.get("enabled") or not policy["modules"].get(module, False)):
        raise ValueError(f"{module} 已在易用配置中关闭")


def compile_settings(base: dict, data: dict) -> dict:
    result = copy.deepcopy(base)
    policy = controls(data)
    result["operator_controls"] = policy
    browser = data["03_浏览器与采集"]
    enabled_browser = browser["内置浏览器" if browser["浏览器选择"] == "内置" else "CDP浏览器"]
    for source in result.get("sources", []):
        allowed = enabled_browser if uses_browser(source) else browser["HTTP采集"]
        if not allowed or not policy["enabled"] or not policy["modules"]["scan"]:
            source["enabled"] = False
        # 区域子集不构成完整来源快照，不能据此停用职位。
        source["sync_active"] = False
    result.setdefault("scan", {}).update(max_workers=browser["同时扫描来源数"], retry_attempts=browser["网络重试次数"], auto_start_windows_chrome=False)
    result.setdefault("application_browser", {})["auto_submit"] = False
    result["application_browser"]["mode"] = "windows_cdp"  # 内置的隔离 Chromium 传输也使用此项。
    result["application_browser"].setdefault("windows_cdp", {})["url_env"] = "CHROME_CDP_URL"
    policy["browser_enabled"] = enabled_browser
    policy["browser_transport"] = "builtin" if browser["浏览器选择"] == "内置" else "cdp"
    policy["headless"] = browser["内置浏览器无界面"]
    result.setdefault("field_mappings", {}).setdefault("safety", {})["allow_submit"] = False
    result.setdefault("email", {})["dry_run"] = True
    return result


def classify_region(location: str) -> str | None:
    text = (location or "").casefold().strip()
    if not text or text in ("remote", "远程", "unknown"):
        return None
    patterns = (
        ("香港", r"hong kong|香港"),
        ("新加坡", r"singapore|新加坡"),
        ("美国", r"united states|\busa?\b|california|texas|santa clara|san jose|austin|chicago|new york|boston|oregon|massachusetts|美国"),
        ("欧洲", r"united kingdom|\buk\b|england|london|cambridge|ireland|germany|france|netherlands|norway|hungary|sweden|finland|denmark|switzerland|belgium|spain|italy|poland|austria|trondheim|budapest|amsterdam|europe|欧洲|英国|德国|挪威|荷兰|匈牙利"),
        ("中国大陆", r"\bchina\b|mainland|beijing|shanghai|shenzhen|guangzhou|中国|北京|上海|深圳|广州|杭州|南京|苏州|武汉|成都|西安|合肥|天津|珠海|东莞|厦门"),
        ("其他地区", r"canada|australia|japan|india|korea|taiwan|macau|brazil|israel|加拿大|澳大利亚|日本|印度|台湾|澳门"),
    )
    found = {name for name, regex in patterns if re.search(regex, text)}
    if "香港" in found:
        found.discard("中国大陆")
    return found.pop() if len(found) == 1 else None


def region_allowed(location: str, config: dict) -> bool:
    policy = config.get("operator_controls")
    if not policy:
        return True
    region = classify_region(location)
    return policy["regions"].get(region or "保留未知地点", False)

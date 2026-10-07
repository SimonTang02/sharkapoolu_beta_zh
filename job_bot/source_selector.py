"""供 CLI 命令和工作流共用的确定性来源选择逻辑。"""

from __future__ import annotations

import re
from typing import Any


CDP_SOURCE_TYPES = {
    "handshake",
    "alibaba_cdp",
    "huawei_cdp",
    "jobsdb_hk",
    "moka_cdp",
    "shixiseng",
    "zhipin",
}


def uses_browser(source: dict[str, Any]) -> bool:
    return bool(source.get("fetch_via_cdp")) or str(
        source.get("type", "")
    ).casefold() in CDP_SOURCE_TYPES


def _folded_set(values: Any) -> set[str]:
    if values in (None, ""):
        return set()
    if not isinstance(values, list):
        raise ValueError("来源选择器的值必须为列表")
    return {str(value).casefold() for value in values}


def select_sources(
    config: dict[str, Any], selector: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    selector = selector or {}
    include_names = _folded_set(selector.get("include_names"))
    exclude_names = _folded_set(selector.get("exclude_names"))
    include_categories = _folded_set(selector.get("include_categories"))
    exclude_categories = _folded_set(selector.get("exclude_categories"))
    include_types = _folded_set(selector.get("include_types"))
    exclude_types = _folded_set(selector.get("exclude_types"))
    include_companies = _folded_set(selector.get("include_companies"))
    name_patterns = [
        re.compile(str(pattern), re.I)
        for pattern in selector.get("include_name_patterns", [])
    ]
    exclude_patterns = [
        re.compile(str(pattern), re.I)
        for pattern in selector.get("exclude_name_patterns", [])
    ]
    browser = str(selector.get("browser", "any")).casefold()
    if browser not in {"any", "http", "cdp"}:
        raise ValueError("来源选择器的浏览器选项必须为 any、http 或 cdp")

    selected: list[dict[str, Any]] = []
    for source in config.get("sources", []):
        if source.get("enabled", True) is False:
            continue
        name = str(source.get("name", ""))
        folded_name = name.casefold()
        category = str(source.get("source_category", "")).casefold()
        source_type = str(source.get("type", "")).casefold()
        company = str(source.get("company", "")).casefold()
        if include_names and folded_name not in include_names:
            continue
        if folded_name in exclude_names:
            continue
        if include_categories and category not in include_categories:
            continue
        if category in exclude_categories:
            continue
        if include_types and source_type not in include_types:
            continue
        if source_type in exclude_types:
            continue
        if include_companies and company not in include_companies:
            continue
        if name_patterns and not any(pattern.search(name) for pattern in name_patterns):
            continue
        if any(pattern.search(name) for pattern in exclude_patterns):
            continue
        if browser == "http" and uses_browser(source):
            continue
        if browser == "cdp" and not uses_browser(source):
            continue
        selected.append(source)
    return selected


def selector_from_cli(args: Any) -> dict[str, Any]:
    """转换可选 argparse 字段，同时避免选择器依赖特定 CLI。"""
    return {
        "include_names": getattr(args, "source", None) or [],
        "exclude_names": getattr(args, "exclude_source", None) or [],
        "include_categories": getattr(args, "source_category", None) or [],
        "include_types": getattr(args, "source_type", None) or [],
        "include_companies": getattr(args, "company", None) or [],
        "browser": getattr(args, "source_browser", None) or "any",
    }

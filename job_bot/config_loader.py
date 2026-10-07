"""支持递归相对路径引用的可组合 JSON 配置。"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any


class ConfigError(RuntimeError):
    pass


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if key in {"includes", "patches"}:
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _patched_value(current: Any, patch: Any, context: str) -> Any:
    """合并补丁；列表值支持显式追加和删除操作。"""
    if isinstance(current, list) and isinstance(patch, dict) and any(
        key.startswith("$") for key in patch
    ):
        allowed = {"$append", "$remove", "$replace"}
        unknown = set(patch) - allowed
        if unknown:
            raise ConfigError(
                f"不支持的列表操作，位置 {context}：{', '.join(sorted(unknown))}"
            )
        if "$replace" in patch:
            replacement = patch["$replace"]
            if not isinstance(replacement, list):
                raise ConfigError(f"$replace 必须是列表，位置：{context}")
            result = list(replacement)
        else:
            result = list(current)
        remove = patch.get("$remove", [])
        append = patch.get("$append", [])
        if not isinstance(remove, list) or not isinstance(append, list):
            raise ConfigError(f"$append/$remove 必须是列表，位置：{context}")
        result = [item for item in result if item not in remove]
        for item in append:
            if item not in result:
                result.append(item)
        return result
    if isinstance(current, dict) and isinstance(patch, dict):
        result = dict(current)
        for key, value in patch.items():
            child_context = f"{context}.{key}" if context else key
            result[key] = (
                _patched_value(result[key], value, child_context)
                if key in result
                else value
            )
        return result
    return patch


def _resolve_path(config: dict[str, Any], dotted_path: str) -> Any:
    current: Any = config
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ConfigError(f"补丁路径不存在：{dotted_path}")
        current = current[part]
    return current


def apply_patches(
    config: dict[str, Any], patches: Any, *, source: Path | None = None
) -> dict[str, Any]:
    """按名称修改列表中的对象，无需复制整个基础列表。"""
    if patches in (None, []):
        return config
    if not isinstance(patches, list):
        raise ConfigError("'patches' 必须是列表")
    result = config
    for index, operation in enumerate(patches):
        context = f"patches[{index}]"
        if source:
            context = f"{source}:{context}"
        if not isinstance(operation, dict):
            raise ConfigError(f"补丁必须是对象：{context}")
        path = str(operation.get("path", "")).strip()
        match = operation.get("match")
        changes = operation.get("set")
        if not path or not isinstance(match, dict) or not match:
            raise ConfigError(f"补丁必须包含 path 和非空 match：{context}")
        if not isinstance(changes, dict):
            raise ConfigError(f"补丁的 'set' 必须是对象：{context}")
        target = _resolve_path(result, path)
        if not isinstance(target, list):
            raise ConfigError(f"补丁目标必须是列表：{path}")
        matched = [
            item
            for item in target
            if isinstance(item, dict)
            and all(item.get(key) == value for key, value in match.items())
        ]
        if len(matched) != 1:
            raise ConfigError(
                f"补丁必须在以下位置匹配到且仅匹配一个项目：{path}；实际匹配数：{len(matched)}: {context}"
            )
        item = matched[0]
        patched = _patched_value(item, changes, f"{path}[{match}]")
        item.clear()
        item.update(patched)
    return result


def load_composed_config(path: Path, _stack: tuple[Path, ...] = ()) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if resolved in _stack:
        chain = " -> ".join(str(item) for item in (*_stack, resolved))
        raise ConfigError(f"配置引用存在循环：{chain}")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"未找到配置文件：{resolved}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"JSON 格式无效：{resolved}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"配置根节点必须是对象：{resolved}")

    merged: dict[str, Any] = {}
    includes = payload.get("includes", [])
    if isinstance(includes, str):
        includes = [includes]
    if not isinstance(includes, list) or not all(isinstance(item, str) for item in includes):
        raise ConfigError(f"'includes' 必须是字符串列表：{resolved}")
    for include in includes:
        child = (resolved.parent / include).resolve()
        merged = deep_merge(
            merged, load_composed_config(child, (*_stack, resolved))
        )
    combined = deep_merge(merged, payload)
    return apply_patches(combined, payload.get("patches", []), source=resolved)


def validate_config(config: dict[str, Any]) -> None:
    sources = config.get("sources", [])
    if not isinstance(sources, list):
        raise ConfigError("'sources' 必须是列表")
    source_names = [str(item.get("name", "")) for item in sources if isinstance(item, dict)]
    if any(not name for name in source_names):
        raise ConfigError("每个来源都必须填写非空名称")
    if len(source_names) != len(set(source_names)):
        raise ConfigError("来源名称必须唯一")
    adapters = config.get("portals", {}).get("adapters", [])
    adapter_ids = [str(item.get("id", "")) for item in adapters if isinstance(item, dict)]
    if any(not adapter_id for adapter_id in adapter_ids):
        raise ConfigError("每个门户适配器都必须填写非空 id")
    if len(adapter_ids) != len(set(adapter_ids)):
        raise ConfigError("门户适配器 id 必须唯一")
    for adapter in adapters:
        adapter_id = str(adapter.get("id", "<unnamed>"))
        if int(adapter.get("timeout_seconds", 180)) < 1:
            raise ConfigError(
                f"门户适配器 {adapter_id!r} timeout_seconds 必须至少为 1"
            )
    company_profiles = config.get("portals", {}).get("company_profiles", [])
    company_profile_ids = [
        str(item.get("id", "")) for item in company_profiles if isinstance(item, dict)
    ]
    if any(not profile_id for profile_id in company_profile_ids):
        raise ConfigError("每个公司门户资料都必须填写非空 id")
    if len(company_profile_ids) != len(set(company_profile_ids)):
        raise ConfigError("公司门户资料 id 必须唯一")
    known_adapter_ids = set(adapter_ids)
    for profile in company_profiles:
        profile_id = str(profile.get("id", "<unnamed>"))
        if str(profile.get("adapter", "")) not in known_adapter_ids:
            raise ConfigError(
                f"公司门户资料 {profile_id!r} 引用了未知适配器"
            )
        if not profile.get("company_patterns"):
            raise ConfigError(
                f"公司门户资料 {profile_id!r} 必须配置 company_patterns"
            )
    allow_submit = config.get("field_mappings", {}).get("safety", {}).get("allow_submit")
    if allow_submit is not False:
        raise ConfigError("field_mappings.safety.allow_submit 必须为 false")
    workers = int(config.get("scan", {}).get("max_workers", 1))
    if workers < 1:
        raise ConfigError("scan.max_workers 必须至少为 1")
    retry_attempts = int(config.get("scan", {}).get("retry_attempts", 0))
    retry_backoff = float(config.get("scan", {}).get("retry_backoff_seconds", 0))
    if retry_attempts < 0:
        raise ConfigError("scan.retry_attempts 不能为负数")
    if retry_backoff < 0:
        raise ConfigError("scan.retry_backoff_seconds 不能为负数")
    lifecycle_guard = config.get("scan", {}).get("lifecycle_guard", {})
    minimum_fraction = float(
        lifecycle_guard.get("minimum_fraction_of_previous", 0.0)
    )
    if not 0.0 <= minimum_fraction <= 1.0:
        raise ConfigError(
            "scan.lifecycle_guard.minimum_fraction_of_previous 必须介于 0 和 1 之间"
        )
    if int(lifecycle_guard.get("minimum_items", 0)) < 0:
        raise ConfigError("scan.lifecycle_guard.minimum_items 不能为负数")
    for source in sources:
        name = str(source.get("name", "<unnamed>"))
        for key in ("max_pages", "page_size"):
            if key in source and int(source[key]) < 1:
                raise ConfigError(f"source {name!r} {key} 必须至少为 1")
        for key in ("request_delay_seconds", "timeout_seconds"):
            if key in source and float(source[key]) < 0:
                raise ConfigError(f"source {name!r} {key} 不能为负数")
    weekly = config.get("reporting", {}).get("weekly", {})
    if str(weekly.get("week_start", "monday")).casefold() != "monday":
        raise ConfigError("reporting.weekly.week_start 当前必须为 'monday'")
    if int(weekly.get("max_items_per_section", 25)) < 1:
        raise ConfigError(
            "reporting.weekly.max_items_per_section 必须至少为 1"
        )
    scoring = config.get("scoring", {})
    algorithm = str(scoring.get("algorithm", "foundation_v2"))
    if algorithm not in {"foundation_v2", "weighted_keywords_v1"}:
        raise ConfigError(f"不支持的 scoring.algorithm：{algorithm}")
    foundations = scoring.get("foundation_groups", [])
    foundation_names = [str(item.get("name", "")) for item in foundations]
    if len(foundation_names) != len(set(foundation_names)):
        raise ConfigError("scoring 基础组名称必须唯一")
    if algorithm == "foundation_v2" and not foundations:
        raise ConfigError("foundation_v2 需要配置 scoring.foundation_groups")
    for foundation in foundations:
        score = int(foundation.get("base_score", -1))
        if not 0 <= score <= 100:
            raise ConfigError("scoring 基础组 base_score 必须介于 0 和 100 之间")
        if not foundation.get("keywords"):
            raise ConfigError("每个 scoring 基础组都必须配置 keywords")
    modifiers = scoring.get("modifiers", [])
    modifier_names = [str(item.get("name", "")) for item in modifiers]
    if len(modifier_names) != len(set(modifier_names)):
        raise ConfigError("scoring 修正项名称必须唯一")
    bands = scoring.get("bands", {"high": 75, "relevant": 60, "adjacent": 45})
    band_values = [int(bands.get(key, 0)) for key in ("high", "relevant", "adjacent")]
    if not (100 >= band_values[0] > band_values[1] > band_values[2] >= 0):
        raise ConfigError("scoring 分档必须满足 100 >= high > relevant > adjacent >= 0")

    strategy = config.get("strategy", {})
    strategy_foundations = {
        str(item.get("name", "")) for item in strategy.get("foundations", [])
    }
    for modifier in strategy.get("score_modifiers", []):
        try:
            re.compile(str(modifier.get("pattern", "")), re.I)
        except re.error as exc:
            raise ConfigError(
                f"strategy 评分修正规则的正则表达式无效 {modifier.get('name')!r}: {exc}"
            ) from exc
        if modifier.get("scope", "all") not in {"all", "title"}:
            raise ConfigError("strategy 评分修正项的 scope 必须为 all 或 title")
    for override in strategy.get("foundation_overrides", []):
        if str(override.get("foundation", "")) not in strategy_foundations:
            raise ConfigError(
                f"strategy 基础组覆盖项引用了未知基础组："
                f"{override.get('foundation')}"
            )
        try:
            re.compile(str(override.get("title_pattern", "")), re.I)
        except re.error as exc:
            raise ConfigError(f"基础组覆盖项的正则表达式无效：{exc}") from exc

    supported_modules = {
        "penn_channels",
        "daily", "scan", "rescore", "digest", "scoring_report", "scoring_experiment", "strategy_full",
        "weekly", "session_audit",
    }
    workflows = config.get("workflows", {})
    if not isinstance(workflows, dict):
        raise ConfigError("workflows 必须是对象")
    for name, workflow in workflows.items():
        modules = workflow.get("modules", []) if isinstance(workflow, dict) else []
        if not modules or not isinstance(modules, list):
            raise ConfigError(f"工作流 {name!r} 必须包含模块列表")
        unknown = set(modules) - supported_modules
        if unknown:
            raise ConfigError(
                f"工作流 {name!r} 包含不支持的模块： {', '.join(sorted(unknown))}"
            )
        if len(modules) != len(set(modules)):
            raise ConfigError(f"工作流 {name!r} 包含重复模块")
        if "max_workers" in workflow and int(workflow["max_workers"]) < 1:
            raise ConfigError(f"工作流 {name!r} max_workers 必须至少为 1")
        browser = str(workflow.get("source_selector", {}).get("browser", "any"))
        if browser not in {"any", "http", "cdp"}:
            raise ConfigError(
                f"工作流 {name!r} source_selector.browser 必须是 any、http 或 cdp"
            )

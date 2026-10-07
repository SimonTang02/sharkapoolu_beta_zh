"""基于配置匹配申请门户并规划适配器命令。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class PortalAdapter:
    id: str
    priority: int
    platforms: tuple[str, ...]
    host_suffixes: tuple[str, ...]
    company_patterns: tuple[str, ...]
    script: str | None
    subcommand: str | None
    env_option: str | None
    default_args: tuple[str, ...]
    dispatchable: bool
    supports_draft: bool
    timeout_seconds: int
    session_probe: dict[str, Any]


def adapters_from_config(config: dict[str, Any]) -> list[PortalAdapter]:
    adapters = []
    for raw in config.get("portals", {}).get("adapters", []):
        adapters.append(
            PortalAdapter(
                id=str(raw["id"]),
                priority=int(raw.get("priority", 0)),
                platforms=tuple(str(item).casefold() for item in raw.get("platforms", [])),
                host_suffixes=tuple(str(item).casefold() for item in raw.get("host_suffixes", [])),
                company_patterns=tuple(str(item) for item in raw.get("company_patterns", [])),
                script=str(raw["script"]) if raw.get("script") else None,
                subcommand=str(raw["subcommand"]) if raw.get("subcommand") else None,
                env_option=str(raw["env_option"]) if raw.get("env_option") else None,
                default_args=tuple(str(item) for item in raw.get("default_args", [])),
                dispatchable=bool(raw.get("dispatchable", True)),
                supports_draft=bool(raw.get("supports_draft", False)),
                timeout_seconds=int(raw.get("timeout_seconds", 180)),
                session_probe=dict(raw.get("session_probe", {})),
            )
        )
    return sorted(adapters, key=lambda item: (-item.priority, item.id))


def _matches(adapter: PortalAdapter, *, company: str, platform: str, url: str) -> bool:
    host = (urlsplit(url).hostname or "").casefold()
    checks: list[bool] = []
    if adapter.platforms:
        checks.append(platform.casefold() in adapter.platforms)
    if adapter.host_suffixes:
        checks.append(
            any(host == suffix or host.endswith(f".{suffix}") for suffix in adapter.host_suffixes)
        )
    if adapter.company_patterns:
        checks.append(any(re.search(pattern, company, re.I) for pattern in adapter.company_patterns))
    return bool(checks) and all(checks)


def resolve_adapter(
    config: dict[str, Any], *, company: str, platform: str, url: str
) -> PortalAdapter | None:
    for adapter in adapters_from_config(config):
        if _matches(adapter, company=company, platform=platform, url=url):
            return adapter
    return None


def resolve_adapter_by_url(config: dict[str, Any], url: str) -> PortalAdapter | None:
    host = (urlsplit(url).hostname or "").casefold()
    for adapter in adapters_from_config(config):
        if adapter.host_suffixes and any(
            host == suffix or host.endswith(f".{suffix}")
            for suffix in adapter.host_suffixes
        ):
            return adapter
    return None


def resolve_company_profile(
    config: dict[str, Any], *, company: str, adapter_id: str
) -> dict[str, Any]:
    """解析叠加在通用 ATS 适配器上的公司专属行为。"""
    profiles = sorted(
        config.get("portals", {}).get("company_profiles", []),
        key=lambda item: -int(item.get("priority", 0)),
    )
    for profile in profiles:
        if str(profile.get("adapter", "")) != adapter_id:
            continue
        patterns = profile.get("company_patterns", [])
        if any(re.search(str(pattern), company, re.I) for pattern in patterns):
            return dict(profile)
    return {}


def adapter_command(
    adapter: PortalAdapter,
    *,
    application_id: int,
    config_path: Path,
    env_path: Path,
) -> list[str] | None:
    if not adapter.dispatchable or not adapter.script:
        return None
    command = [sys.executable, str(ROOT / adapter.script)]
    if adapter.subcommand:
        command.append(adapter.subcommand)
    command.extend(("--application-id", str(application_id), "--config", str(config_path)))
    if adapter.env_option:
        command.extend((adapter.env_option, str(env_path)))
    command.extend(adapter.default_args)
    return command

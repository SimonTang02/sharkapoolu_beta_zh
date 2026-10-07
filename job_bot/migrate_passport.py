#!/usr/bin/env python3
"""规范化被忽略的 passport.env，且不打印机密值。"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from private_paths import CREDENTIALS_FILE  # noqa: E402

DEFAULT_ENV = CREDENTIALS_FILE
DEFAULT_TEMPLATE = ROOT / "job_bot" / "env.template"

LEGACY_ALIASES = {
    "CUHK_CAREERS_SESSION": "PLATFORM_CUHK_CAREERS_COOKIE",
    "CUHK_USERNAME": "PLATFORM_CUHK_CAREERS_USERNAME",
    "CUHK_PASSWORD": "PLATFORM_CUHK_CAREERS_PASSWORD",
    "JOBSDB_HK_SESSION": "PLATFORM_JOBSDB_HK_COOKIE",
    "BOSS_ZHIPIN_SESSION": "PLATFORM_BOSS_ZHIPIN_COOKIE",
    "SHIXISENG_SESSION": "PLATFORM_SHIXISENG_COOKIE",
}
LINKEDIN_COOKIE_NAMES = {"bcookie", "bscookie", "li_at", "li_rm", "liap", "lidc"}
ENV_ENTRY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def parse_entries(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        match = ENV_ENTRY_RE.fullmatch(line)
        if not match:
            raise SystemExit(f"环境文件中存在无效条目： {path}:{line_number}")
        entries[match.group(1)] = match.group(2)
    return entries


def unquote(raw_value: str) -> str:
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def cookie_names(raw_value: str) -> set[str]:
    return {
        name.strip()
        for part in unquote(raw_value).split(";")
        for name, separator, _value in [part.partition("=")]
        if separator and name.strip()
    }


def canonical_name(name: str) -> str:
    if name in LEGACY_ALIASES:
        return LEGACY_ALIASES[name]
    if name.startswith("COMPANY_") and name.endswith("_SESSION"):
        return f"{name[:-len('_SESSION')]}_COOKIE"
    return name


def migrate_entries(entries: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    migrated: dict[str, str] = {}
    warnings: list[str] = []
    ordered_entries = sorted(
        entries.items(), key=lambda item: canonical_name(item[0]) != item[0]
    )
    for old_name, raw_value in ordered_entries:
        name = canonical_name(old_name)
        if (
            old_name == "COMPANY_TEXAS_INSTRUMENTS_SESSION"
            and cookie_names(raw_value) & LINKEDIN_COOKIE_NAMES
        ):
            name = "PLATFORM_LINKEDIN_COOKIE"
            warnings.append(
                "Moved LinkedIn-domain cookie from the legacy TI slot to "
                "PLATFORM_LINKEDIN_COOKIE"
            )
        existing = migrated.get(name, "")
        if not existing or not unquote(existing):
            migrated[name] = raw_value
    return migrated, warnings


def entry_group(name: str) -> tuple[int, str]:
    if name.startswith("COMPANY_"):
        return 0, "Configured company credentials"
    if name.startswith("PLATFORM_"):
        return 1, "Configured platform credentials"
    if name.startswith("SMTP_"):
        return 2, "Configured email delivery"
    return 3, "Other configured values"


def render_template(template: Path, entries: dict[str, str]) -> tuple[str, int, int]:
    """生成经过整理的占位项以及其他已配置的值。"""
    rendered: list[str] = []
    consumed: set[str] = set()
    configured = {
        name: raw_value
        for name, raw_value in entries.items()
        if unquote(raw_value)
    }

    for raw_line in template.read_text(encoding="utf-8").splitlines():
        match = ENV_ENTRY_RE.fullmatch(raw_line.strip())
        if not match:
            rendered.append(raw_line)
            continue
        name = match.group(1)
        rendered.append(f"{name}={configured[name]}" if name in configured else raw_line)
        consumed.add(name)

    current_group = ""
    for name in sorted(set(configured) - consumed, key=lambda item: (entry_group(item)[0], item)):
        group = entry_group(name)[1]
        if group != current_group:
            rendered.extend(["", f"# {group}."])
            current_group = group
        rendered.append(f"{name}={configured[name]}")
    return "\n".join(rendered) + "\n", len(configured), len(consumed - set(configured))


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent, text=True
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
        temporary_path.chmod(0o600)
        temporary_path.replace(path)
        path.chmod(0o600)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="将 passport.env 迁移到当前凭据结构"
    )
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    entries = parse_entries(args.env_file)
    migrated, warnings = migrate_entries(entries)
    content, configured_count, placeholder_count = render_template(args.template, migrated)
    if not args.dry_run:
        write_atomic(args.env_file, content)
    print(
        f"Passport migration {'preview' if args.dry_run else 'complete'}: "
        f"configured={configured_count}, placeholders={placeholder_count}"
    )
    for warning in warnings:
        print(f"警告：{warning}")


if __name__ == "__main__":
    main()

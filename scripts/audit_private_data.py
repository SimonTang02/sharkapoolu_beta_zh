#!/usr/bin/env python3
"""验证候选人信息仅存放在已配置的私有根目录下。"""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from private_paths import (  # noqa: E402
    APPLICATION_KEYWORDS,
    APPLICATION_PROFILE,
    CREDENTIALS_FILE,
    EVIDENCE_PROFILE,
    PRIVATE_ROOT,
)


SKIP_DIRS = {
    ".git",
    ".TinyTeX",
    ".python_packages",
    ".playwright-browsers",
    ".venv",
    "__pycache__",
}
TEXT_SUFFIXES = {
    "",
    ".bib",
    ".cfg",
    ".env",
    ".json",
    ".jsonl",
    ".md",
    ".py",
    ".sh",
    ".sql",
    ".tex",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def private_markers() -> dict[str, str]:
    application = _load(APPLICATION_PROFILE)
    evidence = _load(EVIDENCE_PROFILE)
    fields = application.get("fields", {})
    identity = evidence.get("identity", {})
    markers = {
        "candidate_email": str(identity.get("email") or fields.get("email") or ""),
        "candidate_phone": str(fields.get("phone") or ""),
        "candidate_display_name": str(identity.get("display_name") or ""),
        "candidate_headline_name": str(identity.get("headline_name") or ""),
        "candidate_chinese_name": str(fields.get("chinese_name") or ""),
        "candidate_summary": str(evidence.get("candidate_summary") or ""),
        "candidate_closing_strength": str(evidence.get("closing_strength") or ""),
    }
    for index, group in enumerate(evidence.get("evidence_groups", [])):
        markers[f"evidence_{index}"] = str(group.get("evidence") or "")
    return {
        label: value
        for label, value in markers.items()
        if len(value.strip()) >= 4
    }


def public_text_files():
    for path in ROOT.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            relative = path.relative_to(ROOT)
        except ValueError:
            continue
        if PRIVATE_ROOT == path or PRIVATE_ROOT in path.parents:
            continue
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        if path.suffix.casefold() not in TEXT_SUFFIXES:
            continue
        yield path


def main() -> None:
    missing = [
        path
        for path in (
            CREDENTIALS_FILE,
            APPLICATION_PROFILE,
            EVIDENCE_PROFILE,
            APPLICATION_KEYWORDS,
        )
        if not path.is_file()
    ]
    if missing:
        for path in missing:
            print(f"MISSING_PRIVATE_FILE {path.relative_to(ROOT)}")
        raise SystemExit(1)

    markers = private_markers()
    leaks: list[tuple[Path, str]] = []
    for path in public_text_files():
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for label, value in markers.items():
            if value in content:
                leaks.append((path.relative_to(ROOT), label))

    if leaks:
        for path, label in leaks:
            print(f"PRIVATE_VALUE_OUTSIDE_PRIVATE_ROOT {path} marker={label}")
        raise SystemExit(1)
    print(f"私有数据审计通过：private_root={PRIVATE_ROOT}")


if __name__ == "__main__":
    main()

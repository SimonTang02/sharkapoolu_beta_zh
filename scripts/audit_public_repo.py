#!/usr/bin/env python3
"""审计 Git 将要发布的文件，并可选择检查现有历史记录。"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_PUBLIC_FILE_BYTES = 10 * 1024 * 1024
TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".css",
    ".csv",
    ".html",
    ".ini",
    ".json",
    ".js",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".sql",
    ".sty",
    ".tex",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
FORBIDDEN_SUFFIXES = {
    ".db",
    ".pdf",
    ".sqlite",
    ".sqlite3",
    ".zip",
}
FORBIDDEN_NAMES = {
    ".env",
    "auth.json",
    "gpt_context.md",
    "passport.env",
}
SECRET_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "github_token": re.compile(r"\b(?:gh[opsu]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "slack_token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
}
LOCAL_PATH_PATTERN = re.compile(
    r"(?:(?<![A-Za-z0-9.])/home/[A-Za-z0-9._-]+/|"
    r"[A-Za-z]:[\\/]Users[\\/][^\\/\s]+[\\/])"
)


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def publishable_paths() -> list[Path]:
    output = _git(
        "ls-files", "--cached", "--others", "--exclude-standard", "-z"
    ).stdout
    paths: list[Path] = []
    for raw in output.split(b"\0"):
        if not raw:
            continue
        relative = Path(raw.decode("utf-8", errors="surrogateescape"))
        if (ROOT / relative).is_file():
            paths.append(relative)
    return sorted(set(paths), key=lambda item: item.as_posix())


def private_markers() -> dict[str, str]:
    try:
        from audit_private_data import private_markers as load_private_markers

        return load_private_markers()
    except (FileNotFoundError, KeyError, ValueError):
        return {}


def path_problem(path: Path) -> str | None:
    path_text = path.as_posix()
    lower_name = path.name.casefold()
    if path.parts and path.parts[0] == "private_data" and path_text != "private_data/README.md":
        return "private_data 私有数据内容"
    if lower_name in FORBIDDEN_NAMES:
        return "私有文件或凭据文件名"
    if path.suffix.casefold() in FORBIDDEN_SUFFIXES:
        return "生成的文档、数据库或归档文件"
    if any(part.casefold() in {"browser_profiles", "browser_state"} for part in path.parts):
        return "浏览器会话目录"
    return None


def audit_current_tree() -> list[str]:
    problems: list[str] = []
    markers = private_markers()
    for relative in publishable_paths():
        full_path = ROOT / relative
        if full_path.is_symlink():
            problems.append(f"SYMLINK {relative}")
            continue
        reason = path_problem(relative)
        if reason:
            problems.append(f"FORBIDDEN_PATH {relative} reason={reason}")
            continue
        size = full_path.stat().st_size
        if size > MAX_PUBLIC_FILE_BYTES:
            problems.append(f"LARGE_FILE {relative} bytes={size}")
        if full_path.suffix.casefold() not in TEXT_SUFFIXES:
            continue
        try:
            content = full_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if LOCAL_PATH_PATTERN.search(content):
            problems.append(f"LOCAL_ABSOLUTE_PATH {relative}")
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(content):
                problems.append(f"SECRET_PATTERN {relative} marker={label}")
        for label, value in markers.items():
            if value and value in content:
                problems.append(f"PRIVATE_VALUE {relative} marker={label}")
    return problems


def audit_history() -> list[str]:
    problems: set[str] = set()
    names = _git("log", "--all", "--name-only", "--format=").stdout.decode(
        "utf-8", errors="replace"
    )
    for name in filter(None, (line.strip() for line in names.splitlines())):
        reason = path_problem(Path(name))
        if reason:
            problems.add(f"HISTORY_FORBIDDEN_PATH {name} reason={reason}")

    revisions = _git("rev-list", "--all").stdout.decode().splitlines()
    markers = private_markers()
    for revision in revisions:
        for label, value in markers.items():
            result = _git(
                "grep", "-I", "-l", "-F", "-e", value, revision, "--", ".", check=False
            )
            if result.returncode not in (0, 1):
                raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
            for match in result.stdout.decode("utf-8", errors="replace").splitlines():
                _, _, path = match.partition(":")
                problems.add(f"HISTORY_PRIVATE_VALUE {path} marker={label}")
    return sorted(problems)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--history",
        action="store_true",
        help="同时使用本地私有档案标记检查现有提交",
    )
    args = parser.parse_args()

    problems = audit_current_tree()
    if args.history:
        problems.extend(audit_history())
    if problems:
        for problem in sorted(set(problems)):
            print(problem)
        raise SystemExit(1)
    scope = "工作区和历史记录" if args.history else "可发布的工作区"
    print(f"公开仓库审计通过：{scope}")


if __name__ == "__main__":
    main()

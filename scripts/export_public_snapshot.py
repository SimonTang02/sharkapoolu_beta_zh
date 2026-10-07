#!/usr/bin/env python3
"""将已审计的工作树导出到不含历史记录的新目录。"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

from audit_public_repo import ROOT, audit_current_tree, publishable_paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument(
        "--init",
        action="store_true",
        help="以 main 作为初始分支，初始化空 Git 仓库",
    )
    args = parser.parse_args()

    destination = args.destination.expanduser().resolve()
    if destination.exists():
        raise SystemExit(f"目标路径已存在： {destination}")
    if destination == ROOT or ROOT in destination.parents:
        raise SystemExit("目标路径必须位于源仓库之外")

    problems = audit_current_tree()
    if problems:
        for problem in sorted(set(problems)):
            print(problem)
        raise SystemExit("公开仓库审计失败；未创建快照")

    paths = publishable_paths()
    destination.mkdir(parents=True)
    for relative in paths:
        source = ROOT / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    if args.init:
        subprocess.run(
            ["git", "init", "--initial-branch=main"],
            cwd=destination,
            check=True,
        )
    print(f"已将 {len(paths)} 个文件导出到 {destination}")


if __name__ == "__main__":
    main()

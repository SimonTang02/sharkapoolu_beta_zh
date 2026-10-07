#!/usr/bin/env python3
"""验证并汇总合成后的有效 job-bot 配置。"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from job_bot.bot import load_config  # noqa: E402
from job_bot.run_modules import effective_config_hash  # noqa: E402
from job_bot.source_selector import uses_browser  # noqa: E402


DEFAULT_CONFIG = ROOT / "job_bot/config.china_hk_ic_foreign.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--effective-json",
        type=Path,
        help="写出合并并应用补丁后的有效配置，不包含机密",
    )
    args = parser.parse_args()
    config = load_config(args.config)
    sources = config.get("sources", [])
    enabled = [source for source in sources if source.get("enabled", True) is not False]
    categories = Counter(str(source.get("source_category") or "uncategorized") for source in enabled)
    types = Counter(str(source.get("type") or "unknown") for source in enabled)
    print(f"配置文件：{args.config.resolve()}")
    print(f"SHA-256 哈希：{effective_config_hash(config)}")
    if config.get("experiment"):
        print(f"实验：{config['experiment'].get('name', 'unnamed')}")
    print(
        f"来源：总数={len(sources)} 已启用={len(enabled)} "
        f"http={sum(not uses_browser(source) for source in enabled)} "
        f"cdp={sum(uses_browser(source) for source in enabled)}"
    )
    print("来源类别：")
    for name, count in sorted(categories.items()):
        print(f"  {name}: {count}")
    print("来源类型：")
    for name, count in sorted(types.items()):
        print(f"  {name}: {count}")
    scoring = config.get("scoring", {})
    print(
        f"评分：algorithm={scoring.get('algorithm')} "
        f"purpose={scoring.get('purpose')}"
    )
    for group in scoring.get("foundation_groups", []):
        print(
            f"  基础组={group.get('name')} 基础分={group.get('base_score')} "
            f"关键词数={len(group.get('keywords', []))}"
        )
    strategy = config.get("strategy", {})
    print(
        f"策略：name={strategy.get('name')} purpose={strategy.get('purpose')} "
        f"minimum_score={strategy.get('minimum_score')}"
    )
    print("工作流：")
    for name, workflow in sorted(config.get("workflows", {}).items()):
        print(f"  {name}: {' -> '.join(workflow.get('modules', []))}")
    if args.effective_json:
        args.effective_json.parent.mkdir(parents=True, exist_ok=True)
        args.effective_json.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"有效配置：{args.effective_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""在中国/香港申请批次中使用简历里的香港电话号码。"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from private_paths import APPLICATION_PROFILE, JOBBOT_OUTPUT
from private_paths import CURRENT_RESUME_TEX

RESUME = CURRENT_RESUME_TEX
BASE_PROFILE = APPLICATION_PROFILE
APPLICATIONS = JOBBOT_OUTPUT / "applications"


def update(path: Path, phone: str) -> bool:
    if not path.is_file():
        return False
    profile = json.loads(path.read_text(encoding="utf-8"))
    scopes = profile.get("explicit_authorization", {}).get("location_scopes", [])
    if not {"mainland_china", "hong_kong"}.intersection(scopes):
        return False
    profile.setdefault("fields", {})["phone"] = phone
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return True


def main() -> None:
    resume = RESUME.read_text(encoding="utf-8")
    match = re.search(r"HK:\s*([+\d][\d ()-]+)", resume)
    if not match:
        raise SystemExit("在 current.tex 中未找到明确标注的香港电话号码")
    phone = match.group(1).strip()
    paths = [BASE_PROFILE, *APPLICATIONS.glob("*/profile.json")]
    count = sum(update(path, phone) for path in paths)
    print(f"已为 {count} 个获授权的中国/香港档案更新香港联系号码")


if __name__ == "__main__":
    main()

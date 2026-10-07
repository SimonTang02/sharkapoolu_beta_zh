#!/usr/bin/env python3
"""统一命令行接口，并兼容原版申请机器人。"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from job_bot.application_bot import main as legacy_main  # noqa: E402


def main() -> None:
    commands = {
        "import-manual": "job_bot.manual_database",
        "manual-kit": "application_bot.manual_kit",
        "keywords": "cv.application_keywords",
        "session-audit": "application_bot.session_audit",
        "login-tabs": "application_bot.login_tab_queue",
        "login-preflight": "application_bot.login_preflight",
        "dispatch": "application_bot.dispatcher",
        "platform-audit": "application_bot.platform_audit",
        "tab-manager": "application_bot.tab_manager",
    }
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    module_name = commands.get(command)
    if not module_name:
        legacy_main()
        return
    sys.argv.pop(1)
    module = __import__(module_name, fromlist=["main"])
    raise SystemExit(module.main())


if __name__ == "__main__":
    main()

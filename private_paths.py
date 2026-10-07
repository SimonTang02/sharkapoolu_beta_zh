"""候选人私有数据的规范存储位置。

程序代码可以导入这些路径，但绝不能嵌入候选人具体值。设置 JOBBOT_PRIVATE_DIR
即可迁移整个私有目录树，例如移至加密磁盘或 Synology 挂载目录。
"""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def _default_private_root() -> Path:
    """让源码检出目录保持本地路径，并避免已安装的软件包落入 site-packages。"""
    if (PROJECT_ROOT / "pyproject.toml").is_file():
        return PROJECT_ROOT / "private_data"
    data_home = Path(
        os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    ).expanduser()
    return data_home / "sharkapoolu"


PRIVATE_ROOT = Path(
    os.environ.get("JOBBOT_PRIVATE_DIR", _default_private_root())
).expanduser().resolve()

CREDENTIALS_FILE = PRIVATE_ROOT / "credentials" / "passport.env"
APPLICATION_PROFILE = PRIVATE_ROOT / "profiles" / "application_profile.json"
EVIDENCE_PROFILE = PRIVATE_ROOT / "cv" / "profile" / "evidence_profile.json"
APPLICATION_KEYWORDS = PRIVATE_ROOT / "cv" / "profile" / "application_keywords.json"
PRIVATE_CONFIG = PRIVATE_ROOT / "config" / "job_bot.local.json"
EASY_SETTINGS = PRIVATE_ROOT / "config" / "easy_settings.json"
EASY_RUNTIME_CONFIG = PRIVATE_ROOT / "config" / "easy_runtime.generated.json"

DATABASE_DIR = PRIVATE_ROOT / "database"
JOB_DATABASE = DATABASE_DIR / "china_hk_ic_foreign.sqlite3"
DATABASE_CONNECTION_CONFIG = PRIVATE_ROOT / "config" / "database_connection.json"
DATABASE_BACKUP_DIR = DATABASE_DIR / "backups"
JOBBOT_OUTPUT = PRIVATE_ROOT / "outputs" / "job_bot"
APPLICATION_OUTPUT = PRIVATE_ROOT / "outputs" / "application_bot"
CV_BOT_OUTPUT = PRIVATE_ROOT / "cv" / "reports"

BROWSER_STATE_DIR = PRIVATE_ROOT / "browser" / "state"
BROWSER_PROFILE_DIR = PRIVATE_ROOT / "browser" / "profiles"

RESUME_DIR = PRIVATE_ROOT / "cv" / "source"
RESUME_BUILD_DIR = PRIVATE_ROOT / "cv" / "build"
CURRENT_RESUME_TEX = RESUME_DIR / "current.tex"
VISA_RESUME_TEX = RESUME_DIR / "visa.tex"
CURRENT_RESUME_PDF = RESUME_BUILD_DIR / "current.pdf"
CV_VARIANTS_DIR = PRIVATE_ROOT / "cv" / "variants"
CV_INTAKE_DIR = PRIVATE_ROOT / "cv" / "intake"
MANUAL_DATABASE_DIR = PRIVATE_ROOT / "database" / "manual"


def ensure_private_directories() -> None:
    for path in (
        PRIVATE_ROOT,
        CREDENTIALS_FILE.parent,
        APPLICATION_PROFILE.parent,
        EVIDENCE_PROFILE.parent,
        PRIVATE_CONFIG.parent,
        DATABASE_DIR,
        JOBBOT_OUTPUT,
        APPLICATION_OUTPUT,
        CV_BOT_OUTPUT,
        BROWSER_STATE_DIR,
        BROWSER_PROFILE_DIR,
        RESUME_DIR,
        RESUME_BUILD_DIR,
        CV_VARIANTS_DIR,
        CV_INTAKE_DIR,
        MANUAL_DATABASE_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
        try:
            path.chmod(0o700)
        except OSError:
            pass

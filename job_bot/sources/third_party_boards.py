"""第三方招聘平台适配器。

这些适配器有意采用保守的初始实现。JobsDB、BOSS Zhipin 和实习僧的公开页面差异较大，
且经常动态变化；每个适配器都将平台专属假设集中在一处，便于后续增强解析器或添加
登录/Cookie 流程，而无需修改核心程序。
"""

from __future__ import annotations

from typing import Any


def prepare_jobsdb_hk_source(source: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(source)
    prepared.setdefault("timeout_seconds", 20)
    prepared.setdefault("platform_note", "JobsDB HK public search page")
    return prepared


def prepare_zhipin_source(source: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(source)
    prepared.setdefault("timeout_seconds", 12)
    prepared.setdefault("platform_note", "BOSS Zhipin public page; likely needs login/captcha-safe session support")
    return prepared


def prepare_shixiseng_source(source: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(source)
    prepared.setdefault("timeout_seconds", 20)
    prepared.setdefault("platform_note", "Shixiseng public internship search page")
    return prepared

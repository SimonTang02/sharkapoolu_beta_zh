"""外部 Chrome CDP 会话的健康检查与端点规范化。"""

from __future__ import annotations

import errno
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


class CdpHealthError(RuntimeError):
    """安全且包含可操作建议的 Chrome DevTools 连接错误。"""


@dataclass(frozen=True)
class CdpHealth:
    browser: str
    connect_url: str


def _start_instruction(endpoint: str) -> str:
    return (
        "请启动专用 Windows JobApplyChrome，并在 Windows 本机启用远程调试；"
        "随后检查仅供 WSL 使用的 portproxy 和防火墙规则。"
        f"已配置的 WSL 端点：{endpoint}"
    )


def _rewrite_websocket_authority(endpoint: str, advertised_url: str) -> str:
    endpoint_parts = urllib.parse.urlsplit(endpoint)
    websocket_parts = urllib.parse.urlsplit(advertised_url)
    if websocket_parts.scheme not in {"ws", "wss"} or not websocket_parts.netloc:
        raise CdpHealthError(
            "Chrome 的 /json/version 返回了格式错误的 webSocketDebuggerUrl"
        )
    websocket_scheme = "wss" if endpoint_parts.scheme == "https" else "ws"
    return urllib.parse.urlunsplit(
        (
            websocket_scheme,
            endpoint_parts.netloc,
            websocket_parts.path,
            websocket_parts.query,
            "",
        )
    )


def check_cdp_health(endpoint: str, timeout_seconds: float = 3.0) -> CdpHealth:
    endpoint = endpoint.strip().rstrip("/")
    parts = urllib.parse.urlsplit(endpoint)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise CdpHealthError(
            "Chrome CDP 健康检查需要 HTTP(S) 端点，例如 "
            "http://172.23.0.1:9223"
        )
    version_url = f"{endpoint}/json/version"
    request = urllib.request.Request(
        version_url,
        headers={"Accept": "application/json", "User-Agent": "job-bot-cdp-health/1"},
    )
    # WSL 网关属于本地传输。绝不通过继承的公司或系统 HTTP 代理发送此请求。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            payload = response.read(256 * 1024)
    except urllib.error.HTTPError as exc:
        raise CdpHealthError(
            f"Chrome CDP 健康检查端点返回 HTTP {exc.code}。"
            + _start_instruction(endpoint)
        ) from exc
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise CdpHealthError(
                "连接 Chrome CDP 端点超时。"
                + _start_instruction(endpoint)
            ) from exc
        if isinstance(reason, OSError) and reason.errno == errno.ECONNREFUSED:
            raise CdpHealthError(
                "Chrome CDP 端点拒绝了连接。"
                + _start_instruction(endpoint)
            ) from exc
        raise CdpHealthError(
            f"无法连接 Chrome CDP 端点（{reason}）。"
            + _start_instruction(endpoint)
        ) from exc
    except (socket.timeout, TimeoutError) as exc:
        raise CdpHealthError(
            "连接 Chrome CDP 端点超时。"
            + _start_instruction(endpoint)
        ) from exc
    except OSError as exc:
        raise CdpHealthError(
            f"无法连接 Chrome CDP 端点（{exc}）。"
            + _start_instruction(endpoint)
        ) from exc

    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CdpHealthError(
            "Chrome CDP /json/version 返回了格式错误的 JSON"
        ) from exc
    if not isinstance(data, dict):
        raise CdpHealthError("Chrome CDP /json/version 返回了意外载荷")

    browser = data.get("Browser")
    websocket_url = data.get("webSocketDebuggerUrl")
    if not isinstance(browser, str) or not browser.strip():
        raise CdpHealthError("Chrome CDP /json/version 未包含 Browser 字段")
    if not isinstance(websocket_url, str) or not websocket_url.strip():
        raise CdpHealthError(
            "Chrome CDP /json/version 未包含 webSocketDebuggerUrl 字段"
        )
    if not re.search(r"\b(?:Chrome|Chromium)/\d+", browser, re.I):
        raise CdpHealthError(
            f"端点报告了不受支持的 CDP 浏览器：{browser}"
        )

    return CdpHealth(
        browser=browser.strip(),
        connect_url=_rewrite_websocket_authority(endpoint, websocket_url.strip()),
    )

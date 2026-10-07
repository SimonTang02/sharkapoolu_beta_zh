from __future__ import annotations

import io
import socket
import urllib.error
import unittest
from unittest.mock import Mock, patch

from job_bot.browser_connection import CdpHealthError, check_cdp_health


class CdpHealthTests(unittest.TestCase):
    def _opener_with(self, result) -> Mock:
        opener = Mock()
        if isinstance(result, Exception):
            opener.open.side_effect = result
        else:
            opener.open.return_value = io.BytesIO(result)
        return opener

    @patch("job_bot.browser_connection.urllib.request.build_opener")
    def test_health_rewrites_loopback_websocket_to_portproxy(self, build_opener) -> None:
        build_opener.return_value = self._opener_with(
            b'{"Browser":"Chrome/152.0.1.2",'
            b'"webSocketDebuggerUrl":"ws://127.0.0.1:9222/devtools/browser/abc"}'
        )
        health = check_cdp_health("http://172.23.0.1:9223")
        self.assertEqual(health.browser, "Chrome/152.0.1.2")
        self.assertEqual(
            health.connect_url,
            "ws://172.23.0.1:9223/devtools/browser/abc",
        )

    @patch("job_bot.browser_connection.urllib.request.build_opener")
    def test_health_distinguishes_connection_refused(self, build_opener) -> None:
        build_opener.return_value = self._opener_with(
            urllib.error.URLError(ConnectionRefusedError(111, "refused"))
        )
        with self.assertRaisesRegex(CdpHealthError, "拒绝了连接"):
            check_cdp_health("http://172.23.0.1:9223")

    @patch("job_bot.browser_connection.urllib.request.build_opener")
    def test_health_distinguishes_timeout(self, build_opener) -> None:
        build_opener.return_value = self._opener_with(
            urllib.error.URLError(socket.timeout("timed out"))
        )
        with self.assertRaisesRegex(CdpHealthError, "连接 Chrome CDP 端点超时"):
            check_cdp_health("http://172.23.0.1:9223")

    @patch("job_bot.browser_connection.urllib.request.build_opener")
    def test_health_wraps_connection_reset(self, build_opener) -> None:
        build_opener.return_value = self._opener_with(
            ConnectionResetError(104, "Connection reset by peer")
        )
        with self.assertRaisesRegex(CdpHealthError, "Connection reset by peer"):
            check_cdp_health("http://172.23.0.1:9223")

    @patch("job_bot.browser_connection.urllib.request.build_opener")
    def test_health_rejects_malformed_response(self, build_opener) -> None:
        build_opener.return_value = self._opener_with(b"not-json")
        with self.assertRaisesRegex(CdpHealthError, "格式错误的 JSON"):
            check_cdp_health("http://172.23.0.1:9223")

    @patch("job_bot.browser_connection.urllib.request.build_opener")
    def test_health_rejects_unsupported_browser(self, build_opener) -> None:
        build_opener.return_value = self._opener_with(
            b'{"Browser":"Firefox/145.0",'
            b'"webSocketDebuggerUrl":"ws://172.23.0.1:9223/devtools/browser/abc"}'
        )
        with self.assertRaisesRegex(CdpHealthError, "不受支持的 CDP 浏览器"):
            check_cdp_health("http://172.23.0.1:9223")


if __name__ == "__main__":
    unittest.main()

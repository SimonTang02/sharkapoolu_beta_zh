"""独占隔离的 Chromium 传输；断开外部拥有的 CDP 会话。"""
from __future__ import annotations

from contextlib import contextmanager
import os
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from private_paths import BROWSER_PROFILE_DIR


def checked_endpoint(config: dict) -> str:
    cdp = config.get("application_browser", {}).get("windows_cdp", {})
    endpoint = os.environ.get(cdp.get("url_env", "CHROME_CDP_URL"), "") or cdp.get("url", "")
    parsed = urlsplit(endpoint)
    if parsed.scheme not in ("http", "https", "ws", "wss") or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("易用入口只连接本机CDP；远端Chrome请先通过SSH映射到本机，避免暴露调试端口")
    return endpoint


@contextmanager
def browser_transport(config: dict, needed: bool):
    if not needed:
        yield
        return
    policy = config["operator_controls"]
    if not policy["browser_enabled"]:
        raise ValueError("所选浏览器未开启，请在第三区打开对应开关")
    if policy["browser_transport"] == "cdp":
        checked_endpoint(config)
        yield
        return
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ValueError("请先安装浏览器组件：./scripts/bootstrap.sh --with-browser") from exc
    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    # 使用新建的专用配置，避免意外复用个人 Chrome 会话。
    with tempfile.TemporaryDirectory(prefix="easy_builtin_", dir=BROWSER_PROFILE_DIR) as folder:
        with sync_playwright() as playwright:
            executable = playwright.chromium.executable_path
        command = [executable, "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
                   f"--user-data-dir={folder}", "--no-first-run", "--no-default-browser-check", "about:blank"]
        if policy["headless"]:
            command.insert(1, "--headless=new")
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        previous = os.environ.get("CHROME_CDP_URL")
        try:
            port_file = Path(folder) / "DevToolsActivePort"
            deadline = time.monotonic() + 15
            while not port_file.is_file():
                if process.poll() is not None or time.monotonic() > deadline:
                    raise ValueError("内置浏览器启动失败，请检查Playwright和系统浏览器依赖")
                time.sleep(.1)
            port = int(port_file.read_text().splitlines()[0])
            os.environ["CHROME_CDP_URL"] = f"http://127.0.0.1:{port}"
            yield
        finally:
            if previous is None:
                os.environ.pop("CHROME_CDP_URL", None)
            else:
                os.environ["CHROME_CDP_URL"] = previous
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


class ApplicationMonitor:
    """仅检查已登记的申请页面；绝不关闭外部浏览器。"""
    def __init__(self, config: dict):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ValueError("请先安装Playwright浏览器组件") from exc
        self.playwright = sync_playwright().start()
        try:
            self.browser = self.playwright.chromium.connect_over_cdp(checked_endpoint(config), timeout=2000)
        except Exception:
            self.close()
            raise ValueError("无法监控专用浏览器；自动填写已停止，请检查CDP和Playwright") from None

    def pages(self, application_id: int):
        for context in self.browser.contexts:
            for page in context.pages:
                try:
                    if page.evaluate("window.name") == f"jobbot-application-{application_id}":
                        yield page
                except Exception:
                    continue

    def interruption(self, application_id: int) -> str | None:
        from application_bot.session_audit import classify_session
        matched = False
        for page in self.pages(application_id):
            matched = True
            try:
                state, _ = classify_session(adapter="", url=page.url, title=page.title(),
                    body=page.locator("body").inner_text(timeout=500), http_status=None,
                    password_visible=page.locator("input[type=password]:visible").count() > 0,
                    visible_controls=page.locator("input:visible,select:visible,textarea:visible").count())
                challenge = page.locator('iframe[src*="captcha"]:visible,iframe[src*="duosecurity"]:visible').count()
                if challenge or state in ("challenge_required", "authentication_required", "access_error"):
                    return "challenge_required" if challenge else state
            except Exception:
                return "monitor_unavailable"
        return None if matched else "application_tab_required"

    def screenshot(self, application_id: int, destination: Path) -> bool:
        for page in self.pages(application_id):
            page.screenshot(path=str(destination), full_page=True, timeout=3000)
            destination.chmod(0o600)
            return True
        return False

    def close(self):
        self.playwright.stop()

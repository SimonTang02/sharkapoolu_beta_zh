"""通过已认证的 SSH 标准输入通道访问 SQLite（可选）。

未配置私有连接时，connect() 使用普通 sqlite3。SSH 后端在主机上、数据库旁运行
SQLite 引擎；它不会同步或替换正在使用的数据库文件，也不会重试写入。
"""

from __future__ import annotations

import argparse
import base64
from collections import deque
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import selectors
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

from private_paths import (
    DATABASE_BACKUP_DIR,
    DATABASE_CONNECTION_CONFIG,
    DATABASE_DIR,
    JOB_DATABASE,
)

MAX_MESSAGE_BYTES = 16 * 1024 * 1024
FETCH_BATCH = 64
ERROR_TYPES = {
    name: getattr(sqlite3, name)
    for name in (
        "Error", "DatabaseError", "DataError", "IntegrityError",
        "InterfaceError", "InternalError", "NotSupportedError",
        "OperationalError", "ProgrammingError",
    )
}


def _encode(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"$bytes": base64.b64encode(value).decode("ascii")}
    if isinstance(value, float) and not math.isfinite(value):
        return {"$float": repr(value)}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        return {key: _encode(item) for key, item in value.items()}
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$bytes"}:
            return base64.b64decode(value["$bytes"], validate=True)
        if set(value) == {"$float"}:
            return float(value["$float"])
        return {key: _decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value


def _message(value: Any) -> bytes:
    result = json.dumps(_encode(value), ensure_ascii=True, allow_nan=False,
                        separators=(",", ":")).encode("ascii") + b"\n"
    if len(result) > MAX_MESSAGE_BYTES:
        raise sqlite3.OperationalError("共享数据库消息超过大小限制")
    return result


def _database_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*\.(sqlite3|sqlite|db)", value):
        raise ValueError("数据库必须是带 SQLite 扩展名的文件名")
    return value


def _validate_config(config: dict) -> dict:
    if config.get("mode") == "local":
        return {"mode": "local"}
    if config.get("mode") != "ssh":
        raise ValueError("数据库连接模式必须为 local 或 ssh")
    host = config.get("host", "")
    if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@:-]*", host):
        raise ValueError("请在私有连接配置中设置有效的 SSH 主机别名")
    for name in ("project_dir", "python"):
        if not isinstance(config.get(name), str) or not config[name].strip():
            raise ValueError(f"缺少连接设置： {name}")
        if any(char in config[name] for char in "\n\r\0"):
            raise ValueError(f"连接设置无效： {name}")
    private_dir = config.get("private_dir")
    if private_dir is not None and (
        not isinstance(private_dir, str) or not private_dir.strip()
        or any(char in private_dir for char in "\n\r\0")
    ):
        raise ValueError("连接设置无效： private_dir")
    timeout = config.get("request_timeout", 60)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 300:
        raise ValueError("request_timeout 必须介于 1 到 300 秒之间")
    return {**config, "request_timeout": timeout}


def _remote_path(value: str) -> str:
    if value == "~":
        return '"$HOME"'
    if value.startswith("~/"):
        return '"$HOME"/' + shlex.quote(value[2:])
    return shlex.quote(value)


def ssh_command(config: dict, database: str) -> list[str]:
    config = _validate_config(config)
    command = "cd " + _remote_path(config["project_dir"]) + " && exec "
    if config.get("private_dir"):
        command += "env " + shlex.quote("JOBBOT_PRIVATE_DIR=" + config["private_dir"]) + " "
    command += _remote_path(config["python"]) + " -m job_bot.shared_database serve --database "
    command += shlex.quote(_database_name(database))
    return [
        "ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "ConnectTimeout=10", "-o", "ConnectionAttempts=1",
        "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2",
        "-o", "LogLevel=ERROR", config["host"], command,
    ]


def connect(database: str | Path, *, timeout: float = 5.0):
    """在明确配置后，通过 SSH 路由私有数据库连接。

    外部夹具数据库和 :memory: 仍使用普通 SQLite 行为。配置无效或 SSH 连接失败时会
    抛出异常，绝不会回退到另一份可写的本地数据库。
    """
    raw = str(database)
    path = Path(raw).expanduser().resolve()
    if raw == ":memory:" or path.parent != DATABASE_DIR.resolve():
        return sqlite3.connect(database, timeout=timeout)
    if not DATABASE_CONNECTION_CONFIG.exists():
        return sqlite3.connect(database, timeout=timeout)
    try:
        config = _validate_config(json.loads(DATABASE_CONNECTION_CONFIG.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise sqlite3.OperationalError("私有数据库连接配置无效") from None
    if config["mode"] == "local":
        return sqlite3.connect(database, timeout=timeout)
    ensure_ssh_platform()
    return SSHConnection(ssh_command(config, path.name),
                         request_timeout=config["request_timeout"], timeout=timeout)


def ensure_ssh_platform() -> None:
    if os.name == "nt":
        raise sqlite3.OperationalError("SSH 数据库 RPC 使用 Unix 管道；Windows 上请使用 WSL。本地可写回退已禁用。")


class SSHConnection:
    """本仓库使用的 sqlite3.Connection 子集。"""

    def __init__(self, command: list[str], *, request_timeout: float = 60, timeout: float = 5):
        self.row_factory = None
        self.in_transaction = False
        self.total_changes = 0
        self._request_timeout = request_timeout
        self._closed = False
        self._thread_id = threading.get_ident()
        self._buffer = bytearray()
        self._templates = sqlite3.connect(":memory:")
        try:
            self._process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                             bufsize=0)
            os.set_blocking(self._process.stdin.fileno(), False)
            os.set_blocking(self._process.stdout.fileno(), False)
            self._request({"op": "open", "timeout": min(timeout, 30)})
        except (OSError, sqlite3.Error):
            self._abort()
            raise sqlite3.OperationalError(
                "无法打开共享数据库。请检查 SSH 主机密钥、密钥认证、远程项目目录、"
                "Python 和数据库。本地回退已禁用。"
            ) from None

    def _abort(self) -> None:
        self._closed = True
        process = getattr(self, "_process", None)
        if process is not None:
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        self._templates.close()

    def _request(self, request: dict) -> dict:
        if self._closed:
            raise sqlite3.ProgrammingError("共享数据库连接已关闭")
        if threading.get_ident() != self._thread_id:
            raise sqlite3.ProgrammingError("共享数据库连接必须始终由创建它的线程使用")
        packet = _message(request)
        deadline = time.monotonic() + self._request_timeout
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(self._process.stdin, selectors.EVENT_WRITE)
                offset = 0
                while offset < len(packet):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise TimeoutError
                    offset += os.write(self._process.stdin.fileno(), packet[offset:])
                selector.unregister(self._process.stdin)
                selector.register(self._process.stdout, selectors.EVENT_READ)
                while b"\n" not in self._buffer:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise TimeoutError
                    chunk = os.read(self._process.stdout.fileno(), 65536)
                    if not chunk:
                        raise EOFError
                    self._buffer.extend(chunk)
                    if len(self._buffer) > MAX_MESSAGE_BYTES:
                        raise ValueError
            line, _, rest = self._buffer.partition(b"\n")
            self._buffer = bytearray(rest)
            response = _decode(json.loads(line))
            self.in_transaction = response.get("in_transaction", self.in_transaction)
            self.total_changes = response.get("total_changes", self.total_changes)
        except (OSError, EOFError, TimeoutError, ValueError):
            self._abort()
            raise sqlite3.OperationalError(
                "共享数据库通道失败。如果当时正在写入或提交，结果状态未知；"
                "重试前请先检查已保存的数据。"
            ) from None
        if "error" in response:
            error_type = ERROR_TYPES.get(response["error"], sqlite3.DatabaseError)
            raise error_type("共享数据库拒绝了此操作（" + response["error"] + "）")
        return response

    def cursor(self):
        return SSHCursor(self)

    def execute(self, sql: str, parameters=()):
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql: str, parameters):
        return self.cursor().executemany(sql, parameters)

    def executescript(self, sql: str):
        return self.cursor().executescript(sql)

    def commit(self) -> None:
        self._request({"op": "commit"})

    def rollback(self) -> None:
        self._request({"op": "rollback"})

    def backup(self, target, *, pages: int = -1, progress=None,
               name: str = "main", sleep: float = 0.250) -> None:
        if name != "main" or self.in_transaction:
            raise sqlite3.ProgrammingError("备份要求使用主数据库且当前没有活动事务")
        DATABASE_BACKUP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        response = self._request({"op": "backup_start"})
        temporary = None
        try:
            descriptor, filename = tempfile.mkstemp(dir=DATABASE_BACKUP_DIR, suffix=".sqlite3")
            temporary = Path(filename)
            with os.fdopen(descriptor, "wb") as output:
                while not response["done"]:
                    response = self._request({"op": "backup_read"})
                    output.write(response["chunk"])
            source = sqlite3.connect(temporary)
            try:
                if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise sqlite3.DatabaseError("共享数据库备份完整性检查失败")
                source.backup(target, pages=pages, progress=progress, sleep=sleep)
            finally:
                source.close()
        finally:
            try:
                if not self._closed:
                    self._request({"op": "backup_close"})
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def close(self) -> None:
        if not self._closed:
            try:
                self._request({"op": "close"})
            finally:
                self._abort()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is None:
            try:
                self.commit()
            except sqlite3.Error:
                if not self._closed:
                    self.rollback()
                raise
        else:
            self.rollback()
        return False

    def __del__(self):
        if not getattr(self, "_closed", True):
            self._abort()


class SSHCursor:
    def __init__(self, connection: SSHConnection):
        self.connection = connection
        self.row_factory = connection.row_factory
        self.description = None
        self.lastrowid = None
        self.rowcount = -1
        self.arraysize = 1
        self._id = None
        self._rows = deque()
        self._template = None
        self._closed = False

    def _release(self) -> None:
        if self._id is not None and not self.connection._closed:
            self.connection._request({"op": "cursor_close", "cursor": self._id})
        self._id = None
        if self._template is not None:
            self._template.close()
            self._template = None

    def _execute(self, op: str, sql: str, parameters=None):
        if self._closed:
            raise sqlite3.ProgrammingError("共享数据库游标已关闭")
        self._release()
        request = {"op": op, "sql": sql}
        if parameters is not None:
            request["parameters"] = parameters
        response = self.connection._request(request)
        self.description = tuple(tuple(item) for item in response["description"]) if response["description"] else None
        self.lastrowid = response["lastrowid"]
        self.rowcount = response["rowcount"]
        self._id = response.get("cursor")
        self._rows = deque(response["rows"])
        if self.description and self.row_factory is sqlite3.Row:
            # 原生 sqlite3.Row 会保留列名、重复列、索引、切片、keys() 和 dict(row)，
            # 无需重新实现这些行为。
            columns = ["NULL AS \"" + item[0].replace('"', '""') + "\"" for item in self.description]
            self._template = self.connection._templates.execute("SELECT " + ",".join(columns))
        return self

    def execute(self, sql: str, parameters=()):
        return self._execute("execute", sql, parameters)

    def executemany(self, sql: str, parameters):
        return self._execute("executemany", sql, list(parameters))

    def executescript(self, sql: str):
        return self._execute("executescript", sql)

    def fetchone(self):
        if self._closed:
            raise sqlite3.ProgrammingError("共享数据库游标已关闭")
        if not self._rows and self._id is not None:
            response = self.connection._request({"op": "fetch", "cursor": self._id})
            self._id = response.get("cursor")
            self._rows.extend(response["rows"])
            self.rowcount = response["rowcount"]
        if not self._rows:
            return None
        values = tuple(self._rows.popleft())
        if self.row_factory is sqlite3.Row:
            return sqlite3.Row(self._template, values)
        return self.row_factory(self, values) if self.row_factory else values

    def fetchmany(self, size=None):
        result = []
        for _ in range(self.arraysize if size is None else size):
            row = self.fetchone()
            if row is None:
                break
            result.append(row)
        return result

    def fetchall(self):
        return list(self)

    def close(self) -> None:
        self._release()
        self._rows.clear()
        self._closed = True

    def __iter__(self):
        return self

    def __next__(self):
        row = self.fetchone()
        if row is None:
            raise StopIteration
        return row

    def __del__(self):
        try:
            self._release()
        except (OSError, sqlite3.Error):
            pass


def serve(database: str, *, idle_timeout: float = 900) -> None:
    """一个 SSH 会话独占一个 SQLite 连接和事务范围。"""
    path = DATABASE_DIR / _database_name(database)
    if path.resolve().parent != DATABASE_DIR.resolve():
        raise ValueError("数据库必须位于规范的私有数据库目录内")
    conn = None
    cursors = {}
    cursor_sequence = 0
    snapshot = None
    snapshot_path = None

    def close_snapshot():
        nonlocal snapshot, snapshot_path
        if snapshot is not None:
            snapshot.close()
            snapshot = None
        if snapshot_path is not None:
            snapshot_path.unlink(missing_ok=True)
            snapshot_path = None

    try:
        while True:
            # 客户端断开或被遗弃后，最终必须释放写锁。
            with selectors.DefaultSelector() as selector:
                selector.register(sys.stdin.buffer, selectors.EVENT_READ)
                if not selector.select(idle_timeout):
                    break
            line = sys.stdin.buffer.readline(MAX_MESSAGE_BYTES + 1)
            if not line:
                break
            if len(line) > MAX_MESSAGE_BYTES or not line.endswith(b"\n"):
                break
            stop = False
            try:
                request = _decode(json.loads(line))
                op = request["op"]
                response = {}
                if op == "open" and conn is None:
                    # mode=rw 可防止意外创建第二个数据库。
                    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True,
                                           timeout=request.get("timeout", 5))
                elif conn is None:
                    raise sqlite3.ProgrammingError
                elif op in {"execute", "executemany", "executescript"}:
                    cursor = conn.cursor()
                    try:
                        if op == "executescript":
                            cursor.executescript(request["sql"])
                        else:
                            getattr(cursor, op)(request["sql"], request.get("parameters", []))
                        rows = cursor.fetchmany(FETCH_BATCH) if cursor.description else []
                        response = {"description": cursor.description, "rows": rows,
                                    "lastrowid": cursor.lastrowid, "rowcount": cursor.rowcount}
                        if len(rows) == FETCH_BATCH:
                            cursor_sequence += 1
                            cursors[cursor_sequence] = cursor
                            response["cursor"] = cursor_sequence
                        else:
                            cursor.close()
                    except BaseException:
                        cursor.close()
                        raise
                elif op == "fetch":
                    cursor_id = request["cursor"]
                    cursor = cursors[cursor_id]
                    rows = cursor.fetchmany(FETCH_BATCH)
                    response = {"rows": rows, "rowcount": cursor.rowcount}
                    if len(rows) == FETCH_BATCH:
                        response["cursor"] = cursor_id
                    else:
                        cursors.pop(cursor_id).close()
                elif op == "cursor_close":
                    cursor = cursors.pop(request["cursor"], None)
                    if cursor is not None:
                        cursor.close()
                elif op == "commit":
                    conn.commit()
                elif op == "rollback":
                    conn.rollback()
                elif op == "backup_start":
                    if conn.in_transaction:
                        raise sqlite3.ProgrammingError
                    close_snapshot()
                    DATABASE_BACKUP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
                    descriptor, filename = tempfile.mkstemp(dir=DATABASE_BACKUP_DIR, suffix=".sqlite3")
                    os.close(descriptor)
                    snapshot_path = Path(filename)
                    destination = sqlite3.connect(snapshot_path)
                    try:
                        deadline = time.monotonic() + 45
                        def progress(status, remaining, total):
                            if time.monotonic() > deadline:
                                raise sqlite3.OperationalError
                        conn.backup(destination, pages=256, progress=progress, sleep=0.05)
                    finally:
                        destination.close()
                    snapshot = snapshot_path.open("rb")
                    response = {"done": False}
                elif op == "backup_read":
                    if snapshot is None:
                        raise sqlite3.ProgrammingError
                    chunk = snapshot.read(65536)
                    response = {"chunk": chunk, "done": not chunk}
                    if not chunk:
                        close_snapshot()
                elif op == "backup_close":
                    close_snapshot()
                elif op == "close":
                    stop = True
                else:
                    raise sqlite3.ProgrammingError
            except (sqlite3.Error, KeyError, ValueError, TypeError):
                # 错误文本可能包含 SQL、绑定值或私有路径。
                error_name = sys.exc_info()[0].__name__
                response = {"error": error_name if error_name in ERROR_TYPES else "ProgrammingError"}
            if conn is not None:
                response.update(in_transaction=conn.in_transaction, total_changes=conn.total_changes)
            try:
                packet = _message(response)
            except sqlite3.Error:
                packet = _message({"error": "OperationalError"})
            sys.stdout.buffer.write(packet)
            sys.stdout.buffer.flush()
            if stop:
                break
    finally:
        close_snapshot()
        for cursor in cursors.values():
            cursor.close()
        if conn is not None:
            conn.close()  # 断开连接时回滚尚未提交的操作。


def _write_config(config: dict) -> None:
    DATABASE_CONNECTION_CONFIG.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(dir=DATABASE_CONNECTION_CONFIG.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(config, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, DATABASE_CONNECTION_CONFIG)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    server = commands.add_parser("serve", help="由 SSH 启动的私有协议端点")
    server.add_argument("--database", default=JOB_DATABASE.name)
    server.add_argument("--idle-timeout", type=float, default=900)
    setup = commands.add_parser("configure", help="预览或保存此客户端的连接设置")
    setup.add_argument("--host", required=True, help="数据库主机上已配置的 SSH 别名")
    setup.add_argument("--project-dir", required=True, help="数据库主机上的项目目录")
    setup.add_argument("--python", default=".venv/bin/python")
    setup.add_argument("--private-dir", help="使用 JOBBOT_PRIVATE_DIR 时填写主机上的私有根目录")
    setup.add_argument("--request-timeout", type=float, default=60)
    setup.add_argument("--apply", action="store_true")
    commands.add_parser("check", help="检查已配置的连接，不写入 schema 或数据")
    watch = commands.add_parser("watch", help="报告已提交的更改，不打印数据库内容")
    watch.add_argument("--interval", type=float, default=1.0)
    prepare = commands.add_parser("prepare-host", help="备份主机数据库并启用 WAL")
    prepare.add_argument("--database", default=JOB_DATABASE.name)
    prepare.add_argument("--apply", action="store_true")
    local = commands.add_parser("local", help="预览或显式恢复本地连接模式")
    local.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "serve":
            serve(args.database, idle_timeout=args.idle_timeout)
        elif args.command == "configure":
            config = {"mode": "ssh", "host": args.host, "project_dir": args.project_dir,
                      "python": args.python, "request_timeout": args.request_timeout}
            if args.private_dir:
                config["private_dir"] = args.private_dir
            config = _validate_config(config)
            print("模式：SSH；私有数据库使用主机上的同名文件。")
            print("不会复制数据库、凭据、个人资料或浏览器文件。")
            if args.apply:
                _write_config(config)
                print("已保存私有连接配置。运行工作流前请先执行 check。")
            else:
                print("仅预览。添加 --apply 可保存此客户端的配置。")
        elif args.command in {"check", "watch"}:
            if not JOB_DATABASE.is_file():
                config = json.loads(DATABASE_CONNECTION_CONFIG.read_text(encoding="utf-8")) if DATABASE_CONNECTION_CONFIG.exists() else {"mode": "local"}
                if _validate_config(config)["mode"] == "local":
                    raise ValueError("检查前请先初始化本地数据库")
            if args.command == "watch" and not 0.2 <= args.interval <= 60:
                raise ValueError("监视间隔必须介于 0.2 到 60 秒之间")
            conn = connect(JOB_DATABASE)
            try:
                conn.execute("SELECT 1").fetchone()
                version = conn.execute("PRAGMA data_version").fetchone()[0]
                print(f"数据库连接正常；data_version={version}")
                if args.command == "watch":
                    print("正在监视已提交的更改；按 Ctrl+C 停止。", flush=True)
                    try:
                        while True:
                            time.sleep(args.interval)
                            current = conn.execute("PRAGMA data_version").fetchone()[0]
                            if current != version:
                                print("数据库更新时间：" + datetime.now(timezone.utc).isoformat(), flush=True)
                                version = current
                    except KeyboardInterrupt:
                        pass
            finally:
                conn.close()
        elif args.command == "local":
            if args.apply:
                _write_config({"mode": "local"})
                print("已保存本地连接模式。现有本地文件未刷新。")
            else:
                print("预览：恢复本地连接。仅在数据库主机上添加 --apply，")
                print("或在单独恢复并检查当前数据库备份后再添加。")
        elif args.command == "prepare-host":
            path = DATABASE_DIR / _database_name(args.database)
            if not path.is_file():
                raise ValueError("准备主机前请先初始化主机数据库")
            if DATABASE_CONNECTION_CONFIG.exists():
                config = _validate_config(json.loads(DATABASE_CONNECTION_CONFIG.read_text(encoding="utf-8")))
                if config["mode"] != "local":
                    raise ValueError("prepare-host 要求使用 local 连接模式")
            print("计划：创建私有 SQLite 备份、检查完整性并启用 WAL。")
            if not args.apply:
                print("仅预览。添加 --apply 可准备数据库主机。")
                return
            DATABASE_BACKUP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
            backup = DATABASE_BACKUP_DIR / f"{path.stem}.{stamp}.sqlite3"
            descriptor = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            source = sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True, timeout=10)
            destination = sqlite3.connect(backup)
            try:
                deadline = time.monotonic() + 60
                def progress(status, remaining, total):
                    if time.monotonic() > deadline:
                        raise TimeoutError("主机备份超时；请在数据库活动较少时重试")
                source.backup(destination, pages=256, progress=progress, sleep=0.05)
                if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("备份完整性检查失败")
                mode = source.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                if mode != "wal":
                    raise ValueError("无法启用 WAL；请停止正在运行的工作流后重试")
            finally:
                destination.close()
                source.close()
            print(f"主机已就绪；备份保存在 database/backups/；journal_mode={mode}")
    except (OSError, ValueError, TypeError, AttributeError, TimeoutError, sqlite3.Error) as exc:
        if args.command == "serve":
            raise SystemExit(1) from None
        # 有意省略可能包含私有值的异常文本。
        print(f"数据库操作失败（{type(exc).__name__}）。请检查本地路径，"
              "SSH 设置和连接配置。", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

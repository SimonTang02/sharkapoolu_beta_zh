"""针对跨进程 SQLite 操作的合成集成测试。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from job_bot import shared_database as shared
from job_bot.bot import ensure_schema


class SharedDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "database" / "fixture.sqlite3"
        self.path.parent.mkdir()
        self.local = sqlite3.connect(self.path)
        self.local.execute("PRAGMA journal_mode=WAL")
        self.local.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT UNIQUE, content BLOB)")
        self.local.commit()
        self.addCleanup(self.local.close)

    def remote(self, *, idle_timeout=900):
        command = [sys.executable, "-m", "job_bot.shared_database", "serve",
                   "--database", "fixture.sqlite3", "--idle-timeout", str(idle_timeout)]
        with patch.dict(os.environ, {"JOBBOT_PRIVATE_DIR": str(self.root)}):
            conn = shared.SSHConnection(command, request_timeout=5)
        self.addCleanup(conn.close)
        return conn

    def test_committed_changes_visible_in_both_directions(self):
        first = self.remote()
        second = self.remote()
        first.execute("INSERT INTO items(name) VALUES (?)", ("Example Company",))
        self.assertEqual(second.execute("SELECT count(*) FROM items").fetchone()[0], 0)
        first.commit()
        self.assertEqual(second.execute("SELECT name FROM items").fetchone()[0], "Example Company")
        second.execute("UPDATE items SET name=?", ("Example Updated",))
        second.commit()
        self.assertEqual(first.execute("SELECT name FROM items").fetchone()[0], "Example Updated")
        self.local.execute("UPDATE items SET name=?", ("Example Host",))
        self.local.commit()
        self.assertEqual(first.execute("SELECT name FROM items").fetchone()[0], "Example Host")

    def test_context_commits_and_rolls_back(self):
        conn = self.remote()
        with conn:
            conn.execute("INSERT INTO items(name) VALUES (?)", ("Example Company",))
        self.assertEqual(self.local.execute("SELECT count(*) FROM items").fetchone()[0], 1)
        with self.assertRaises(RuntimeError):
            with conn:
                conn.execute("UPDATE items SET name=?", ("Example Rollback",))
                raise RuntimeError("Synthetic failure")
        self.assertEqual(self.local.execute("SELECT name FROM items").fetchone()[0], "Example Company")

    def test_disconnect_rolls_back_uncommitted_work(self):
        conn = self.remote()
        conn.execute("INSERT INTO items(name) VALUES ('Example Company')")
        conn._abort()
        self.assertEqual(self.local.execute("SELECT count(*) FROM items").fetchone()[0], 0)

    def test_idle_connection_releases_transaction(self):
        conn = self.remote(idle_timeout=0.1)
        conn.execute("INSERT INTO items(name) VALUES ('Example Company')")
        conn._process.wait(timeout=5)
        self.assertEqual(self.local.execute("SELECT count(*) FROM items").fetchone()[0], 0)
        with self.assertRaises(sqlite3.OperationalError):
            conn.commit()

    def test_native_row_factory_tuple_blob_and_named_parameters(self):
        conn = self.remote()
        content = b"\x00\xffsynthetic"
        conn.execute("INSERT INTO items(name, content) VALUES (:name,:content)",
                     {"name": "Example Company", "content": content})
        conn.row_factory = sqlite3.Row
        row = conn.execute('SELECT id, name AS "Name", content FROM items').fetchone()
        self.assertIsInstance(row, sqlite3.Row)
        self.assertEqual(row["name"], "Example Company")
        self.assertEqual(row["content"], content)
        self.assertEqual(row[0], 1)
        self.assertEqual(dict(row)["Name"], "Example Company")
        self.assertEqual(row.keys(), ["id", "Name", "content"])

    def test_result_paging_and_cursor_reuse(self):
        conn = self.remote()
        cursor = conn.executemany("INSERT INTO items(name) VALUES (?)",
                                 ((f"Example {index}",) for index in range(200)))
        self.assertEqual(cursor.rowcount, 200)
        conn.commit()
        cursor.execute("SELECT id FROM items ORDER BY id")
        self.assertEqual(cursor.fetchone(), (1,))
        self.assertEqual(cursor.fetchmany(2), [(2,), (3,)])
        self.assertEqual(cursor.fetchall(), [(index,) for index in range(4, 201)])
        self.assertIsNone(cursor.fetchone())
        cursor.execute("SELECT count(*) FROM items")
        self.assertEqual(cursor.fetchone(), (200,))
        cursor.close()
        with self.assertRaises(sqlite3.ProgrammingError):
            cursor.fetchone()

    def test_row_factory_can_be_set_on_cursor(self):
        conn = self.remote()
        cursor = conn.cursor()
        cursor.row_factory = lambda cursor, row: dict(zip((item[0] for item in cursor.description), row))
        self.assertEqual(cursor.execute("SELECT 1 AS id").fetchone(), {"id": 1})

    def test_lastrowid_changes_and_in_transaction(self):
        conn = self.remote()
        cursor = conn.execute("INSERT INTO items(name) VALUES ('Example Company')")
        self.assertEqual(cursor.lastrowid, 1)
        self.assertEqual(conn.total_changes, 1)
        self.assertTrue(conn.in_transaction)
        conn.rollback()
        self.assertFalse(conn.in_transaction)

    def test_integrity_error_has_no_private_values(self):
        conn = self.remote()
        conn.execute("INSERT INTO items(name) VALUES ('Example Company')")
        with self.assertRaises(sqlite3.IntegrityError) as error:
            conn.execute("INSERT INTO items(name) VALUES ('Example Company')")
        self.assertNotIn("Example Company", str(error.exception))
        self.assertNotIn("INSERT", str(error.exception))
        conn.rollback()

    def test_repository_schema_runs_over_protocol(self):
        conn = self.remote()
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)
        job = conn.execute("INSERT INTO jobs(title, url) VALUES (?,?)",
                           ("Example Role", "https://example.com/role"))
        application = conn.execute("INSERT INTO applications(job_id) VALUES (?)", (job.lastrowid,))
        conn.commit()
        self.assertEqual(conn.execute("SELECT status FROM applications WHERE id=?",
                                      (application.lastrowid,)).fetchone()["status"], "draft")

    def test_wal_backup_is_consistent_and_temporary_files_are_removed(self):
        self.local.execute("INSERT INTO items(name) VALUES ('Example Company')")
        self.local.commit()
        conn = self.remote()
        target = sqlite3.connect(self.root / "backup.sqlite3")
        self.addCleanup(target.close)
        with patch.object(shared, "DATABASE_BACKUP_DIR", self.root / "client-backups"):
            conn.backup(target)
        self.assertEqual(target.execute("SELECT name FROM items").fetchone()[0], "Example Company")
        self.assertEqual(target.execute("PRAGMA quick_check").fetchone()[0], "ok")
        self.assertEqual(list((self.root / "client-backups").iterdir()), [])
        self.assertEqual(list((self.path.parent / "backups").iterdir()), [])

    def test_channel_failure_is_not_retried(self):
        conn = self.remote()
        conn._process.kill()
        conn._process.wait()
        with self.assertRaisesRegex(sqlite3.OperationalError, "结果状态未知"):
            conn.commit()
        self.assertTrue(conn._closed)

    def test_connection_rejects_use_from_another_thread(self):
        conn = self.remote()
        errors = []
        def execute():
            try:
                conn.execute("SELECT 1")
            except sqlite3.Error as exc:
                errors.append(exc)
        worker = threading.Thread(target=execute)
        worker.start()
        worker.join()
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], sqlite3.ProgrammingError)
        self.assertEqual(conn.execute("SELECT 1").fetchone(), (1,))

    def test_missing_host_database_is_not_created(self):
        command = [sys.executable, "-m", "job_bot.shared_database", "serve",
                   "--database", "missing.sqlite3"]
        with patch.dict(os.environ, {"JOBBOT_PRIVATE_DIR": str(self.root)}):
            with self.assertRaises(sqlite3.OperationalError):
                shared.SSHConnection(command, request_timeout=5)
        self.assertFalse((self.path.parent / "missing.sqlite3").exists())

    def test_data_version_detects_changes_from_other_connections(self):
        conn = self.remote()
        before = conn.execute("PRAGMA data_version").fetchone()[0]
        self.local.execute("INSERT INTO items(name) VALUES ('Example Company')")
        self.local.commit()
        after = conn.execute("PRAGMA data_version").fetchone()[0]
        self.assertNotEqual(before, after)

    def test_no_config_preserves_local_sqlite(self):
        with patch.object(shared, "DATABASE_DIR", self.path.parent), \
                patch.object(shared, "DATABASE_CONNECTION_CONFIG", self.root / "absent.json"):
            conn = shared.connect(self.path)
        self.addCleanup(conn.close)
        self.assertIsInstance(conn, sqlite3.Connection)

    def test_invalid_config_fails_without_local_fallback(self):
        config_path = self.root / "connection.json"
        config_path.write_text('{"mode":"invalid"}')
        with patch.object(shared, "DATABASE_DIR", self.path.parent), \
                patch.object(shared, "DATABASE_CONNECTION_CONFIG", config_path):
            with self.assertRaises(sqlite3.OperationalError):
                shared.connect(self.path)

    def test_ssh_route_uses_correct_filename_and_quoted_paths(self):
        config = {"mode": "ssh", "host": "example-db", "project_dir": "~/Example Project",
                  "python": ".venv/bin/python"}
        config_path = self.root / "connection.json"
        config_path.write_text(json.dumps(config))
        with patch.object(shared, "DATABASE_DIR", self.path.parent), \
                patch.object(shared, "DATABASE_CONNECTION_CONFIG", config_path), \
                patch.object(shared, "SSHConnection") as remote:
            shared.connect(self.path)
            command = remote.call_args.args[0]
        self.assertEqual(command[-2], "example-db")
        self.assertIn('cd "$HOME"/\'Example Project\'', command[-1])
        self.assertTrue(command[-1].endswith("--database fixture.sqlite3"))
        self.assertIn("StrictHostKeyChecking=yes", command)

    def test_host_alias_options_and_path_traversal_are_rejected(self):
        for host in ("-oProxyCommand=bad", "host;bad", "host\nother"):
            with self.assertRaises(ValueError):
                shared.ssh_command({"mode": "ssh", "host": host, "project_dir": "repo",
                                    "python": "python3"}, "fixture.sqlite3")
        with self.assertRaises(ValueError):
            shared.ssh_command({"mode": "ssh", "host": "example-db", "project_dir": "repo",
                                "python": "python3"}, "../fixture.sqlite3")

    def test_nonprivate_fixture_databases_remain_local(self):
        conn = shared.connect(self.root / "separate.sqlite3")
        self.addCleanup(conn.close)
        self.assertIsInstance(conn, sqlite3.Connection)

    def test_config_write_is_private_and_atomic(self):
        target = self.root / "config" / "connection.json"
        with patch.object(shared, "DATABASE_CONNECTION_CONFIG", target):
            shared._write_config({"mode": "local"})
        self.assertEqual(json.loads(target.read_text()), {"mode": "local"})
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(target.parent.iterdir()), [target])


if __name__ == "__main__":
    unittest.main()

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from job_bot.daily_pipeline import (
    cdp_endpoint,
    current_run_results,
    ensure_cdp,
    failure_category,
    load_state,
    write_json_atomic,
)


class DailyPipelineTests(unittest.TestCase):
    def test_cdp_endpoint_prefers_environment_override(self) -> None:
        config = {
            "application_browser": {
                "mode": "windows_cdp",
                "windows_cdp": {
                    "url_env": "TEST_PIPELINE_CDP",
                    "url": "http://config:9223",
                },
            }
        }
        with mock.patch.dict(
            "os.environ", {"TEST_PIPELINE_CDP": "http://env:9223"}, clear=False
        ):
            self.assertEqual(cdp_endpoint(config), "http://env:9223")

    def test_no_cdp_sources_needs_no_browser(self) -> None:
        ok, message = ensure_cdp(
            {"sources": [{"name": "RSS", "type": "rss"}]},
            auto_start=False,
            wait_seconds=1,
        )
        self.assertTrue(ok)
        self.assertIn("没有启用", message)

    def test_state_round_trip_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            write_json_atomic(path, {"last_started_at": "2026-09-02T00:00:00+00:00"})
            self.assertEqual(
                load_state(path)["last_started_at"], "2026-09-02T00:00:00+00:00"
            )
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_failure_categories_separate_user_and_robot_actions(self) -> None:
        self.assertEqual(
            failure_category("HTTP Error 403: Forbidden"),
            "authentication_or_access",
        )
        self.assertEqual(
            failure_category("HTTP Error 504: Gateway Time-out"),
            "transient_retry_exhausted",
        )
        self.assertEqual(
            failure_category("expected selector #jobs was not found"),
            "adapter_or_layout",
        )

    def test_current_run_results_excludes_older_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "jobs.sqlite3"
            conn = sqlite3.connect(database)
            conn.execute(
                """
                CREATE TABLE scan_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_name TEXT, status TEXT, jobs_seen INTEGER,
                    jobs_new INTEGER, error TEXT, started_at TEXT, finished_at TEXT
                )
                """
            )
            rows = [
                ("old", "error", 0, 0, "old error", "2026-09-01T00:00:00+00:00", None),
                ("new", "error", 0, 0, "transient", "2026-09-02T00:00:00+00:00", "failed"),
                ("new", "ok", 3, 1, None, "2026-09-02T00:00:00+00:00", "done"),
            ]
            conn.executemany(
                """
                INSERT INTO scan_runs (
                    source_name, status, jobs_seen, jobs_new, error, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
            conn.close()
            result = current_run_results(database, "2026-09-02T00:00:00+00:00")
            self.assertEqual([row["source_name"] for row in result], ["new"])
            self.assertEqual(result[0]["status"], "ok")


if __name__ == "__main__":
    unittest.main()

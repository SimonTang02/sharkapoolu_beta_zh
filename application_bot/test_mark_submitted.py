"""候选人尚未创建批次时，也可以记录人工确认。"""

import contextlib
import io
import sqlite3
import unittest
from unittest.mock import patch

from application_bot import mark_submitted
from job_bot.bot import ensure_schema


class ManualSubmissionTests(unittest.TestCase):
    def test_first_confirmation_initializes_optional_campaign_tables(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)
        job_id = conn.execute(
            "INSERT INTO jobs(title,url,company) VALUES (?,?,?)",
            ("Example Role", "https://example.com/role", "Example Company"),
        ).lastrowid
        application_id = conn.execute(
            "INSERT INTO applications(job_id,status) VALUES (?,'queued')", (job_id,)
        ).lastrowid
        conn.commit()
        with patch.object(mark_submitted, "connect_db", return_value=conn), \
             patch.object(mark_submitted, "load_config", return_value={}), \
             patch("sys.argv", ["mark_submitted", str(application_id)]), \
             contextlib.redirect_stdout(io.StringIO()):
            mark_submitted.main()
        row = conn.execute("SELECT status,submitted_at FROM applications").fetchone()
        self.assertEqual(row["status"], "submitted")
        self.assertTrue(row["submitted_at"])
        self.assertEqual(conn.execute("SELECT count(*) FROM application_events").fetchone()[0], 1)

"""通过实际 schema、资料和 CV 接口验证公开示例。"""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from application_bot.batch_campaign import ensure_campaign_schema
from cv.bot.bot import render_tailored_resume
from job_bot.bot import ensure_schema
from job_bot.private_config import validate_application_profile, validate_evidence_profile
from job_bot.shared_database import _validate_config


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class PublicTemplateTests(unittest.TestCase):
    def test_strategy_cli_reads_database_selected_by_private_overlay(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "database" / "alternate.sqlite3"
            database.parent.mkdir()
            conn = sqlite3.connect(database)
            conn.row_factory = sqlite3.Row
            ensure_schema(conn)
            conn.close()
            config = root / "runtime.json"
            config.write_text(json.dumps({"database": {"path": str(database)}}))
            result = subprocess.run(
                [sys.executable, "-m", "job_bot.strategy_report", "--config", str(config),
                 "--out-dir", str(root / "outputs")],
                env={**os.environ, "JOBBOT_PRIVATE_DIR": str(root)},
                capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(list((root / "outputs").glob("*.md")))

    def test_empty_sql_template_matches_runtime_tables_and_can_be_initialized(self):
        template = sqlite3.connect(":memory:")
        runtime = sqlite3.connect(":memory:")
        self.addCleanup(template.close)
        self.addCleanup(runtime.close)
        template.row_factory = runtime.row_factory = sqlite3.Row
        template.executescript((EXAMPLES / "database_template.sql").read_text())
        ensure_schema(runtime)
        ensure_campaign_schema(runtime)
        tables = [row[0] for row in runtime.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )]
        for table in tables:
            self.assertEqual(template.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0], 0)
            self.assertEqual(
                [tuple(row) for row in template.execute(f'PRAGMA table_info("{table}")')],
                [tuple(row) for row in runtime.execute(f'PRAGMA table_info("{table}")')],
            )
        ensure_schema(template)
        job = template.execute("INSERT INTO jobs(title,url) VALUES (?,?)",
                               ("Example Role", "https://example.com/role"))
        template.execute("INSERT INTO applications(job_id) VALUES (?)", (job.lastrowid,))
        self.assertEqual(template.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_fictional_profiles_are_valid_and_resume_can_be_tailored(self):
        application = json.loads((EXAMPLES / "application_profile_template.json").read_text())
        evidence = json.loads((EXAMPLES / "evidence_profile_template.json").read_text())
        issues = validate_application_profile(application) + validate_evidence_profile(evidence)
        self.assertEqual([issue for issue in issues if issue.level == "ERROR"], [])
        self.assertEqual(application["fields"]["email"], evidence["identity"]["email"])
        self.assertFalse(application["safety"]["allow_submit"])
        rendered = render_tailored_resume(
            evidence, (EXAMPLES / "resume_template.tex").read_text(), "Software Engineer", [],
            keyword_selection={"technical": []},
        )
        self.assertIn("Mike Malon", rendered)
        self.assertIn("New York University", rendered)
        self.assertIn("Fictional resume example", rendered)

    def test_connection_template_supports_local_default_and_ssh_settings(self):
        connection = json.loads((EXAMPLES / "database_connection_template.json").read_text())
        self.assertEqual(_validate_config(connection), {"mode": "local"})
        connection["mode"] = "ssh"
        self.assertEqual(_validate_config(connection)["host"], "example-db")

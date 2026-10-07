import unittest

from application_bot.tab_manager import is_session_anchor, render_report


class SessionAnchorTests(unittest.TestCase):
    def test_workday_candidate_home_is_anchor(self):
        self.assertTrue(
            is_session_anchor(
                "https://example.wd5.myworkdayjobs.com/en-US/External/userHome",
                "Candidate Home",
            )
        )

    def test_job_detail_is_not_anchor(self):
        self.assertFalse(
            is_session_anchor(
                "https://example.wd5.myworkdayjobs.com/en-US/External/job/JR123",
                "Digital Design Engineer",
            )
        )


class ReportTests(unittest.TestCase):
    def test_empty_audit_report_records_no_closure(self):
        report = render_report(
            [], adopted=[], applied=False, closed=[], max_tabs=15
        )
        self.assertIn("模式：仅审计", report)
        self.assertIn("已关闭标签页：0", report)


if __name__ == "__main__":
    unittest.main()

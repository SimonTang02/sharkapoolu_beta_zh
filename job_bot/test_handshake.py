from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from job_bot.sources.handshake import (
    assert_student_page, canonical_job_url, collect, fetch_handshake, normalize_detail, wait_for_search,
)
from job_bot.source_selector import uses_browser, select_sources


class HandshakeTests(unittest.TestCase):
    def test_same_posting_has_stable_identity_across_hosts_and_tracking(self):
        expected = ("123", "https://app.joinhandshake.com/jobs/123")
        for url in ["https://upenn.joinhandshake.com/stu/jobs/123?ref=email",
                    "https://upenn.joinhandshake.com/job-search/123?page=2",
                    "https://app.joinhandshake.com/public/jobs/123#details"]:
            self.assertEqual(canonical_job_url(url), expected)
        for url in ["https://app.joinhandshake.com.evil.test/stu/jobs/123",
                    "https://app.joinhandshake.com/stu/postings",
                    "https://user@app.joinhandshake.com/stu/jobs/123",
                    "https://app.joinhandshake.com/stu/jobs/123/apply"]:
            with self.assertRaises(ValueError):
                canonical_job_url(url)

    def test_login_and_challenge_are_errors_not_empty_success(self):
        for url, text in [("https://upenn.joinhandshake.com/login", ""),
                          ("https://weblogin.pennkey.upenn.edu/idp/", ""),
                          ("https://app.joinhandshake.com/stu/postings", "Continue with email"),
                          ("https://app.joinhandshake.com/stu/postings", "Verify you are human")]:
            with self.assertRaises(RuntimeError):
                assert_student_page(url, text)
        assert_student_page("https://app.joinhandshake.com/stu/postings", "Jobs")

    def test_structured_details_preserve_real_employer_and_location(self):
        row = normalize_detail({"url": "https://upenn.joinhandshake.com/stu/jobs/987", "title": "View job"}, {
            "structured": [{"title": "ASIC Intern — Summer 2027", "hiringOrganization": {"name": "Chip Company"},
                            "description": "<p>RTL verification</p>", "datePosted": "2026-09-17",
                            "jobLocation": [{"address": {"addressLocality": "Austin", "addressCountry": "US"}}]}]})
        self.assertEqual(row["company"], "Chip Company")
        self.assertEqual(row["location"], "Austin, US")
        self.assertEqual(row["published_at"], "2026-09-17")

    def test_unknown_facts_stay_unknown(self):
        row = normalize_detail({"url": "https://app.joinhandshake.com/stu/jobs/11", "title": "RTL Intern"}, {})
        self.assertEqual(row["company"], "")
        self.assertEqual(row["location"], "")
        self.assertEqual(row["published_at"], "")

    def test_bounded_source_cannot_expire_jobs(self):
        with self.assertRaisesRegex(ValueError, "sync_active=false"):
            fetch_handshake({"sync_active": True}, {})

    def test_browser_selection_and_dispatch(self):
        source = {"name": "Penn", "type": "handshake"}
        self.assertTrue(uses_browser(source))
        self.assertEqual(select_sources({"sources": [source]}, {"browser": "http"}), [])
        from job_bot.bot import fetch_source
        with patch("job_bot.sources.handshake.fetch_handshake", return_value=[]) as fetch:
            self.assertEqual(fetch_source(source, {}), [])
            fetch.assert_called_once_with(source, {})

    def test_login_during_search_stops_before_reading_cards(self):
        page = Mock()
        page.url = "https://upenn.joinhandshake.com/login"
        page.goto.return_value.status = 200
        page.locator.return_value.inner_text.return_value = "Continue with email"
        with self.assertRaisesRegex(RuntimeError, "需要登录"):
            collect(page, {"url": "https://upenn.joinhandshake.com/stu/postings"})
        page.locator.return_value.evaluate_all.assert_not_called()

    def test_unknown_empty_layout_does_not_succeed(self):
        page = Mock()
        page.url = "https://app.joinhandshake.com/stu/postings"
        page.goto.return_value.status = 200
        page.locator.return_value.inner_text.return_value = "Jobs — layout changed"
        page.locator.return_value.evaluate_all.return_value = []
        with patch("job_bot.sources.handshake.wait_for_search"):
            with self.assertRaisesRegex(RuntimeError, "未返回可识别的职位卡片"):
                collect(page, {"url": "https://upenn.joinhandshake.com/stu/postings"})

    def test_search_waits_for_query_and_stable_results_after_loading(self):
        page = Mock()
        page.url = "https://upenn.joinhandshake.com/job-search"
        page.locator.return_value.inner_text.return_value = "Jobs"
        ready = {"query": "ASIC", "urls": ["https://upenn.joinhandshake.com/job-search/2"], "loading": False}
        page.evaluate.side_effect = [
            {"query": "", "urls": ["/job-search/1"], "loading": False},
            {"query": "ASIC", "urls": [], "loading": True},
            ready, ready, ready,
        ]
        wait_for_search(page, "ASIC")
        self.assertEqual(page.evaluate.call_count, 5)

    def test_portal_is_audited_but_never_dispatched(self):
        from job_bot.bot import load_config, DEFAULT_CONFIG
        from application_bot.portal_registry import resolve_adapter
        config = load_config(DEFAULT_CONFIG)
        adapter = resolve_adapter(config, company="Chip Company", platform="handshake",
                                  url="https://app.joinhandshake.com/jobs/123")
        self.assertEqual(adapter.id, "handshake")
        self.assertFalse(adapter.dispatchable)
        self.assertFalse(adapter.supports_draft)


if __name__ == "__main__":
    unittest.main()

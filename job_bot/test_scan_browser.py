import os
import unittest
from unittest.mock import Mock, patch

from job_bot.scan_browser import close_scan_page, new_scan_page


class ScanBrowserTests(unittest.TestCase):
    def test_default_retains_normal_lifecycle(self):
        context = Mock()
        with patch.dict(os.environ, {"JOBBOT_SCAN_TARGET_ID": ""}):
            page = new_scan_page(context)
            self.assertIs(page, context.new_page.return_value)
            close_scan_page(page)
            page.close.assert_called_once()

    def test_owned_page_is_reused_without_closing_other_pages(self):
        other, owned = Mock(), Mock()
        other.is_closed.return_value = owned.is_closed.return_value = False
        context = Mock(pages=[other, owned])
        with patch.dict(os.environ, {"JOBBOT_SCAN_TARGET_ID": "owned"}), patch(
            "job_bot.scan_browser.target_id", side_effect=lambda p: "owned" if p is owned else "other"
        ):
            self.assertIs(new_scan_page(context), owned)
            close_scan_page(owned)
            context.new_page.assert_not_called()
            owned.close.assert_not_called()
            other.close.assert_not_called()

    def test_lost_owned_page_does_not_fall_back(self):
        context = Mock(pages=[])
        with patch.dict(os.environ, {"JOBBOT_SCAN_TARGET_ID": "lost"}):
            with self.assertRaisesRegex(RuntimeError, "不可用"):
                new_scan_page(context)
            context.new_page.assert_not_called()

    def test_foreign_page_cannot_be_closed(self):
        page = Mock()
        with patch.dict(os.environ, {"JOBBOT_SCAN_TARGET_ID": "owned"}), patch(
            "job_bot.scan_browser.target_id", return_value="other"
        ):
            with self.assertRaisesRegex(RuntimeError, "拒绝"):
                close_scan_page(page)
            page.close.assert_not_called()

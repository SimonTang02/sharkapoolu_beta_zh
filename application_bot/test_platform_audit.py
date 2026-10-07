from __future__ import annotations

import unittest
from pathlib import Path

from application_bot.platform_audit import classify_portal, platform_for_url
from job_bot.bot import load_config


class PlatformAuditTests(unittest.TestCase):
    def test_platform_mapping(self) -> None:
        config = load_config(
            Path(__file__).resolve().parents[1]
            / "job_bot/config.china_hk_ic_foreign.json"
        )
        self.assertEqual(
            platform_for_url("https://global-external-amd.icims.com/jobs/1/login", config),
            "amd_icims",
        )
        self.assertEqual(
            platform_for_url("https://nxp.wd3.myworkdayjobs.com/careers/job/1", config),
            "workday",
        )
        self.assertEqual(
            platform_for_url(
                "https://monolithicpower.wd12.myworkdayjobs.com/MPS_Careers/job/1",
                config,
            ),
            "workday",
        )

    def test_hcaptcha_blocks_before_login_form(self) -> None:
        state, _, action = classify_portal(
            platform="amd_icims",
            url="https://global-external-amd.icims.com/jobs/1/login",
            title="",
            text="I accept. Protected by hCaptcha Privacy Terms",
            visible_inputs=2,
            visible_file_inputs=0,
        )
        self.assertEqual(state, "captcha_or_consent_required")
        self.assertIn("人工审阅", action)

    def test_mediatek_final_submit_is_not_draft(self) -> None:
        state, _, _ = classify_portal(
            platform="mediatek",
            url="https://careers.mediatek.com/jobs/1",
            title="Role",
            text="确认后，您的申请将被提交审阅。 返回 提交申请",
            visible_inputs=0,
            visible_file_inputs=0,
        )
        self.assertEqual(state, "final_submit_only")

    def test_form_detection(self) -> None:
        state, _, _ = classify_portal(
            platform="ti_oracle",
            url="https://careers.ti.com/apply",
            title="Apply",
            text="Application",
            visible_inputs=4,
            visible_file_inputs=1,
        )
        self.assertEqual(state, "form_detected")

    def test_login_title_is_authentication_required(self) -> None:
        state, _, _ = classify_portal(
            platform="amd_icims",
            url="https://example.icims.com/jobs/1",
            title="Login | GDPR",
            text="Enter Your Information",
            visible_inputs=1,
            visible_file_inputs=0,
        )
        self.assertEqual(state, "authentication_required")

    def test_create_account_title_is_authentication_required(self) -> None:
        state, _, _ = classify_portal(
            platform="mps_workday",
            url="https://monolithicpower.wd12.myworkdayjobs.com/apply",
            title="Create Account",
            text="Email Password Create Account",
            visible_inputs=5,
            visible_file_inputs=0,
        )
        self.assertEqual(state, "authentication_required")

    def test_simplify_search_is_helper_not_application_form(self) -> None:
        state, _, _ = classify_portal(
            platform="simplify",
            url="https://simplify.jobs/search?q=Hardware",
            title="Apply to Jobs",
            text="Search jobs",
            visible_inputs=2,
            visible_file_inputs=0,
        )
        self.assertEqual(state, "discovery_helper")


if __name__ == "__main__":
    unittest.main()

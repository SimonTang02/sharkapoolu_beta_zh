from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from job_bot.application_bot import hydrate_known_resume_contacts, resolve_browser_connection
from job_bot.applications.nvidia_workday import (
    attempt_workday_login,
    attempt_workday_saved_password_login,
    fill_application_questions,
    local_browser_environment,
    recover_workday_authenticated_session,
    recover_workday_transient_error,
    workday_sign_in_required,
)
from application_bot.profile_policy import (
    apply_explicit_authorization,
    has_confirmed_work_permission_scope,
    is_work_permission_question,
    location_in_authorized_scope,
)


class BrowserConnectionConfigTests(unittest.TestCase):
    def test_local_browser_exposes_windows_cjk_fonts_when_available(self) -> None:
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp:
            project_root = Path(temp)
            windows_fonts = project_root / "windows-fonts"
            windows_fonts.mkdir()
            (windows_fonts / "msyh.ttc").touch()
            environment = local_browser_environment(project_root, windows_fonts)
            config_path = Path(environment["FONTCONFIG_FILE"])
            config = config_path.read_text(encoding="utf-8")
            self.assertIn(str(windows_fonts), config)
            self.assertIn("Microsoft YaHei", config)

    def test_login_is_not_attempted_without_credentials(self) -> None:
        page = MagicMock()
        self.assertEqual(
            attempt_workday_login(page, "", ""),
            {"attempted": False, "succeeded": False, "error": ""},
        )

    def test_saved_password_login_is_not_attempted_without_sign_in_boundary(self) -> None:
        page = MagicMock()
        with patch(
            "job_bot.applications.nvidia_workday.workday_sign_in_required",
            return_value=False,
        ):
            self.assertEqual(
                attempt_workday_saved_password_login(page),
                {
                    "attempted": False,
                    "succeeded": False,
                    "credentials_available": False,
                    "error": "",
                },
            )
        page.locator.assert_not_called()

    def test_stale_sign_in_reloads_when_tenant_has_candidate_home(self) -> None:
        page = MagicMock()
        page.url = "https://tenant.myworkdayjobs.com/site/job/example/apply"
        home = MagicMock()
        home.url = "https://tenant.myworkdayjobs.com/site/userHome"
        page.context.pages = [page, home]
        with patch(
            "job_bot.applications.nvidia_workday.workday_sign_in_required",
            side_effect=lambda candidate: candidate is page and page.reload.call_count == 0,
        ):
            result = recover_workday_authenticated_session(page)
        self.assertTrue(result["attempted"])
        self.assertTrue(result["succeeded"])
        page.reload.assert_called_once()

    def test_create_account_title_requires_authentication(self) -> None:
        page = MagicMock()
        page.title.return_value = "Create Account"
        page.url = "https://tenant.myworkdayjobs.com/site/job/example"
        page.locator.return_value.count.return_value = 0
        self.assertTrue(workday_sign_in_required(page))

    def test_application_controls_override_stale_create_account_title(self) -> None:
        page = MagicMock()
        page.title.return_value = "Create Account"
        page.url = "https://tenant.myworkdayjobs.com/site/job/example/apply/autofillWithResume"
        empty = MagicMock()
        empty.count.return_value = 0
        page.get_by_role.return_value = empty
        controls = page.locator.return_value
        controls.count.return_value = 1
        controls.nth.return_value.is_visible.return_value = True
        self.assertFalse(workday_sign_in_required(page))

    def test_sign_in_heading_wins_over_login_page_progress_bar(self) -> None:
        page = MagicMock()
        page.title.return_value = "NVIDIA Careers"
        page.url = "https://tenant.myworkdayjobs.com/site/job/example/apply/autofillWithResume"
        heading = MagicMock()
        heading.count.return_value = 1
        heading.nth.return_value.is_visible.return_value = True
        page.get_by_role.side_effect = lambda role, **kwargs: (
            heading if role == "heading" and kwargs.get("name") == "Sign In" else MagicMock(count=lambda: 0)
        )
        self.assertTrue(workday_sign_in_required(page))

    def test_transient_workday_error_is_reloaded_once(self) -> None:
        page = MagicMock()
        body = page.locator.return_value
        body.inner_text.side_effect = [
            "Something went wrong. Please refresh the page and then try again.",
            "My Information",
        ]
        with patch(
            "job_bot.applications.nvidia_workday.workday_sign_in_required",
            return_value=False,
        ):
            result = recover_workday_transient_error(page)
        self.assertTrue(result["detected"])
        self.assertTrue(result["reloaded"])
        self.assertTrue(result["recovered"])
        page.reload.assert_called_once()

    def test_explicit_authorization_is_scoped_and_submit_stays_disabled(self) -> None:
        profile = {"safety": {"allow_submit": False}}
        apply_explicit_authorization(
            profile,
            gender="male",
            work_authorized=True,
            sponsorship_required=False,
            scopes=["mainland_china", "hong_kong"],
            amd_privacy_accepted=True,
        )
        self.assertTrue(profile["safety"]["allow_sensitive_answers"])
        self.assertFalse(profile["safety"]["allow_submit"])
        self.assertTrue(
            location_in_authorized_scope(
                "Shanghai, China", profile["explicit_authorization"]["location_scopes"]
            )
        )
        self.assertFalse(
            location_in_authorized_scope(
                "Singapore", profile["explicit_authorization"]["location_scopes"]
            )
        )

    def test_location_scope_rejects_mixed_countries_and_separates_hong_kong(self) -> None:
        self.assertFalse(location_in_authorized_scope("Hong Kong, China", ["mainland_china"]))
        self.assertTrue(location_in_authorized_scope("Hong Kong, China", ["hong_kong"]))
        self.assertFalse(location_in_authorized_scope("China, Shanghai; United States, Austin", ["mainland_china", "hong_kong"]))
        self.assertFalse(location_in_authorized_scope("China, Shanghai / Singapore", ["mainland_china"]))
        self.assertFalse(location_in_authorized_scope("China, Shanghai, London", ["mainland_china"]))
        self.assertFalse(location_in_authorized_scope("Hong Kong, London", ["hong_kong"]))
        self.assertTrue(location_in_authorized_scope("China, Shanghai; China, Beijing", ["mainland_china"]))
        self.assertFalse(location_in_authorized_scope("", ["mainland_china"]))

    def test_workday_blocks_legal_answers_without_confirmed_job_scope(self) -> None:
        question = "Are you legally authorized to work in the country where this position is located?"
        profile = {
            "fields": {"country": "China"},  # 居住地不能作为在美国工作的资格证明。
            "custom_answers": {question: "Yes"},
            "explicit_authorization": {
                "user_confirmed": True,
                "location_scopes": ["mainland_china", "hong_kong"],
            },
        }
        for location, allowed in [("Austin, United States", False), ("", False), ("Shanghai, China", True)]:
            with self.subTest(location=location):
                page = MagicMock()
                fields = MagicMock()
                fields.count.return_value = 1
                fields.nth.return_value.inner_text.return_value = question
                fields.nth.return_value.locator.return_value.first.count.return_value = 1
                empty = MagicMock()
                empty.count.return_value = 0
                page.locator.side_effect = lambda selector: fields if selector == '[data-automation-id^="formField"]' else empty
                with patch('job_bot.applications.nvidia_workday._select_single_choice', return_value=True) as select:
                    result = fill_application_questions(page, profile, job_location=location)
                if allowed:
                    select.assert_called_once()
                    self.assertEqual(result['answered'][0]['answer'], 'Yes')
                else:
                    select.assert_not_called()
                    self.assertEqual(result['pending'][0]['reason'], 'work_permission_location_unconfirmed')

    def test_work_permission_needs_explicit_confirmation_and_detects_sponsorship(self) -> None:
        self.assertFalse(has_confirmed_work_permission_scope(
            {"explicit_authorization": {"location_scopes": ["mainland_china"]}},
            "Shanghai, China",
        ))
        self.assertTrue(is_work_permission_question("Will you require employer support to obtain or maintain authorization to work in that country?"))
        self.assertTrue(is_work_permission_question("Will you now or in the future require visa sponsorship?"))
        self.assertFalse(is_work_permission_question("What is your country of residence?"))

    def test_resume_contact_hydration_only_fills_empty_fields(self) -> None:
        profile = {"fields": {"first_name": "Existing", "email": ""}}
        resume = r"""
        \name{Alex EXAMPLE, Lex}
        Email: person@example.com
        US: +1 (215) 555-0100
        \href{https://www.linkedin.com/in/example/}{LinkedIn}
        """
        hydrated = hydrate_known_resume_contacts(profile, resume)
        self.assertEqual(hydrated["fields"]["first_name"], "Existing")
        self.assertEqual(hydrated["fields"]["last_name"], "Example")
        self.assertEqual(hydrated["fields"]["preferred_name"], "Lex")
        self.assertEqual(hydrated["fields"]["email"], "person@example.com")
        self.assertIn("215", hydrated["fields"]["phone"])
        self.assertEqual(
            hydrated["fields"]["linkedin_url"],
            "https://www.linkedin.com/in/example/",
        )

    def test_local_mode_does_not_implicitly_use_cdp_environment(self) -> None:
        config = {"application_browser": {"mode": "local_persistent"}}
        with patch.dict("os.environ", {"CHROME_CDP_URL": "http://gateway:9223"}):
            self.assertEqual(
                resolve_browser_connection(config),
                ("local_persistent", ""),
            )

    def test_windows_cdp_uses_named_environment_endpoint(self) -> None:
        config = {
            "application_browser": {
                "mode": "windows_cdp",
                "windows_cdp": {
                    "url_env": "JOB_CHROME_ENDPOINT",
                    "url": "http://fallback:9223",
                },
            }
        }
        with patch.dict(
            "os.environ", {"JOB_CHROME_ENDPOINT": "http://172.23.0.1:9223"}
        ):
            self.assertEqual(
                resolve_browser_connection(config),
                ("windows_cdp", "http://172.23.0.1:9223"),
            )

    def test_mode_override_can_return_to_original_local_flow(self) -> None:
        config = {
            "application_browser": {
                "mode": "windows_cdp",
                "windows_cdp": {"url": "http://172.23.0.1:9223"},
            }
        }
        self.assertEqual(
            resolve_browser_connection(config, mode_override="local_persistent"),
            ("local_persistent", ""),
        )


if __name__ == "__main__":
    unittest.main()

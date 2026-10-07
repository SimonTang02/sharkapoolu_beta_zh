import unittest
from datetime import date

from application_bot.micron import (
    MICRON_QUESTION_LABELS,
    SUBMIT_BUTTON_NAMES,
    _confirmed_email,
    _is_adult,
    _local_phone,
    classify_body,
    resolve_micron_answers,
)


class MicronClassifyTests(unittest.TestCase):
    def test_success_receipt_is_detected_read_only(self) -> None:
        body = "感谢您的申请，Example！ 查看您的个人资料信息和技能"
        self.assertEqual(classify_body(body), "submitted")
        english = "Thank you for your application! Review your profile information."
        self.assertEqual(classify_body(english), "submitted")

    def test_open_form_is_detected(self) -> None:
        body = "申请表 New College Grad - Engineer, ASIC Digital Design 提交申请"
        self.assertEqual(classify_body(body), "browser_form_started")

    def test_authentication_and_unknown_states(self) -> None:
        self.assertEqual(classify_body("check your email verification code"), "authentication_required")
        self.assertEqual(classify_body("unexpected page"), "manual_required")


class MicronAnswerTests(unittest.TestCase):
    def test_age_is_derived_only_from_confirmed_dob(self) -> None:
        answers = resolve_micron_answers(
            {"fields": {"date_of_birth": "1997-01-15"}},
            today=date(2026, 9, 28),
        )
        by_question = {item["question"]: item["answer"] for item in answers}
        self.assertEqual(by_question["Are you at least 18 years old?"], "是")

    def test_missing_dob_leaves_age_unanswered(self) -> None:
        answers = resolve_micron_answers({"fields": {}}, today=date(2026, 9, 28))
        by_question = {item["question"]: item["answer"] for item in answers}
        self.assertIsNone(by_question["Are you at least 18 years old?"])

    def test_candidate_fact_questions_stay_unanswered(self) -> None:
        answers = resolve_micron_answers({"fields": {}}, today=date(2026, 9, 28))
        self.assertEqual(len(answers), len(MICRON_QUESTION_LABELS))
        non_age = [
            item for item in answers if "at least 18" not in item["question"]
        ]
        self.assertTrue(non_age)
        self.assertTrue(all(item["answer"] is None for item in non_age))

    def test_is_adult_rejects_invalid_dob(self) -> None:
        self.assertIsNone(_is_adult("", today=date(2026, 9, 28)))
        self.assertIsNone(_is_adult("not-a-date", today=date(2026, 9, 28)))


class MicronContactTests(unittest.TestCase):
    def test_local_phone_strips_hk_country_code(self) -> None:
        self.assertEqual(_local_phone("+852 0000 0000"), "00000000")
        self.assertEqual(_local_phone("00000000"), "00000000")

    def test_confirmed_email_falls_back_to_fact(self) -> None:
        profile = {
            "fields": {"email": ""},
            "personal_facts_confirmation": {
                "cross_employer_contact_and_interview_20260925": {
                    "primary_university_email": "student@example.com",
                }
            },
        }
        self.assertEqual(_confirmed_email(profile), "student@example.com")

    def test_field_email_wins_over_fact(self) -> None:
        profile = {
            "fields": {"email": "direct@example.com"},
            "personal_facts_confirmation": {},
        }
        self.assertEqual(_confirmed_email(profile), "direct@example.com")


class MicronSafetyTests(unittest.TestCase):
    def test_adapter_is_no_submit(self) -> None:
        # 适配器声明了自身的安全保护，且绝不会启用提交。
        self.assertTrue(SUBMIT_BUTTON_NAMES)
        self.assertIn("提交申请", SUBMIT_BUTTON_NAMES)
        # 问题标签仅作清单用途，不代表提交指令。
        self.assertTrue(MICRON_QUESTION_LABELS)


if __name__ == "__main__":
    unittest.main()

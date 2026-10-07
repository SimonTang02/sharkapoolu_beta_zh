import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_bot.private_config import (
    EXAMPLES,
    TEMPLATES,
    initialize_private_tree,
    validate_application_profile,
    validate_evidence_profile,
    validate_keyword_library,
)


class PrivateConfigTests(unittest.TestCase):
    def test_checked_in_examples_are_valid_and_templates_exist(self) -> None:
        validators = {
            "application_profile.json": validate_application_profile,
            "evidence_profile.json": validate_evidence_profile,
            "application_keywords.json": validate_keyword_library,
        }
        for filename, validator in validators.items():
            with self.subTest(filename=filename):
                data = json.loads((EXAMPLES / filename).read_text(encoding="utf-8"))
                errors = [issue for issue in validator(data) if issue.level == "ERROR"]
                self.assertEqual(errors, [])
        for source in TEMPLATES.values():
            with self.subTest(source=source):
                self.assertTrue(source.is_file())

    def test_application_profile_rejects_submit_and_bad_types(self) -> None:
        profile = {
            "schema_version": 1,
            "fields": {"email": "person@example.com"},
            "documents": {},
            "education": "not-a-list",
            "safety": {
                "allow_sensitive_answers": False,
                "allow_server_draft": False,
                "allow_submit": True,
            },
        }
        issues = validate_application_profile(profile)
        messages = "\n".join(f"{item.path} {item.message}" for item in issues)
        self.assertIn("application_profile.education 必须是列表", messages)
        self.assertIn("application_profile.safety.allow_submit 必须保持为 false", messages)

    def test_keyword_library_rejects_unknown_preset_references(self) -> None:
        library = {
            "schema_version": 1,
            "sources": {},
            "technical_keywords": [],
            "collaboration_personality_keywords": [],
            "role_presets": [
                {
                    "id": "rtl",
                    "technical_ids": ["missing"],
                    "collaboration_ids": [],
                }
            ],
        }
        issues = validate_keyword_library(library)
        self.assertTrue(any("引用了未知的关键词 ID" in item.message for item in issues))

    def test_keyword_library_reports_malformed_reference_lists(self) -> None:
        library = {
            "schema_version": 1,
            "sources": {},
            "technical_keywords": [
                {
                    "id": "rtl",
                    "english": "RTL",
                    "chinese": "RTL",
                    "evidence": "Reviewed evidence",
                    "example_en": "RTL design",
                    "source_ids": None,
                    "claim_status": "reviewed",
                }
            ],
            "collaboration_personality_keywords": [],
            "role_presets": [
                {
                    "id": "rtl",
                    "technical_ids": None,
                    "collaboration_ids": [],
                    "role_match_terms": "RTL",
                }
            ],
        }
        issues = validate_keyword_library(library)
        messages = "\n".join(f"{item.path} {item.message}" for item in issues)
        self.assertIn("source_ids 必须是字符串列表", messages)
        self.assertIn("technical_ids 必须是字符串列表", messages)
        self.assertIn("role_match_terms 必须是字符串列表", messages)

    def test_initializer_keeps_existing_files_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "profile.json"
            source = root / "example.json"
            destination.write_text('{"existing": true}\n', encoding="utf-8")
            source.write_text(json.dumps({"existing": False}), encoding="utf-8")
            with patch("job_bot.private_config.TEMPLATES", {destination: source}), patch(
                "job_bot.private_config.ensure_private_directories"
            ):
                created, skipped = initialize_private_tree()
            self.assertEqual(created, [])
            self.assertEqual(skipped, [destination])
            self.assertTrue(json.loads(destination.read_text())["existing"])


if __name__ == "__main__":
    unittest.main()

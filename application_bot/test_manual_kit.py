from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from application_bot.manual_kit import render_kit


class ManualKitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.output = self.root / "output"
        self.patch = patch("application_bot.manual_kit.PRIVATE_ROOT", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.manifest = {"title": "Example Campaign", "jobs": []}
        for rank, kind in ((7, "full_time"), (3, "internship")):
            folder = f"{rank:02d}_Example"
            (self.source / folder).mkdir()
            pdf = folder + "/resume.pdf"
            content = b"synthetic PDF test fixture, not a real reviewed document"
            (self.source / pdf).write_bytes(content)
            data = {"rank": rank, "common_fields": {"Example answer": "A < B & C", "Pending": None},
                    "regional_authorization": {"Authorization": "Unconfirmed"}}
            (self.source / folder / "Application_Data.json").write_text(json.dumps(data))
            self.manifest["jobs"].append({"rank": rank, "folder": folder, "company": "Example Company",
                "title": "Example Role", "url": "https://example.test/jobs/example",
                "role_kind": kind, "pdfs": [{"document": "resume", "pdf": pdf,
                "sha256": hashlib.sha256(content).hexdigest(), "visual_review": "passed"}]})
        self.path = self.source / "Manifest.json"

    def render(self, manifest=None, key="example-campaign-"):
        self.path.write_text(json.dumps(self.manifest if manifest is None else manifest))
        return render_kit(self.path, self.output, key)

    def test_preserves_nonconsecutive_ranks_and_manifest_order(self):
        result = self.render()
        text = (self.output / "index.html").read_text(encoding='utf-8')
        self.assertEqual(result["jobs"], 2)
        self.assertLess(text.index('data-rank="7"'), text.index('data-rank="3"'))
        self.assertNotIn('data-rank="1"', text)
        self.assertEqual(json.loads((self.output / "Manifest.json").read_text(encoding='utf-8')), self.manifest)
        self.assertEqual((self.output / "07_Example/resume.pdf").read_bytes(),
                         (self.source / "07_Example/resume.pdf").read_bytes())
        self.assertEqual((self.output / "index.html").stat().st_mode & 0o777, 0o600)

    def test_escapes_answers_and_retains_unconfirmed_values(self):
        self.render()
        text = (self.output / "07_Example/index.html").read_text(encoding='utf-8')
        self.assertIn("A &lt; B &amp; C", text)
        self.assertIn("Unconfirmed", text)
        self.assertIn("未提供：仅如实填写，不能编造", text)

    def test_refuses_existing_output_without_modifying_files(self):
        self.output.mkdir()
        sentinel = self.output / "index.html"
        sentinel.write_text("existing kit and progress address")
        with self.assertRaises(FileExistsError):
            self.render()
        self.assertEqual(sentinel.read_text(encoding='utf-8'), "existing kit and progress address")

    def test_hash_mismatch_and_unreviewed_pdf_fail_before_output(self):
        for change in ({"sha256": "0" * 64}, {"visual_review": "pending"}):
            manifest = copy.deepcopy(self.manifest)
            manifest["jobs"][0]["pdfs"][0].update(change)
            with self.assertRaises(ValueError):
                self.render(manifest)
            self.assertFalse(self.output.exists())

    def test_rejects_duplicate_ranks_and_external_materials(self):
        for field, value in (("rank", 3), ("folder", "../outside"), ("url", "javascript:alert(1)")):
            manifest = copy.deepcopy(self.manifest)
            manifest["jobs"][0][field] = value
            with self.assertRaises(ValueError):
                self.render(manifest)
            self.assertFalse(self.output.exists())
        manifest = copy.deepcopy(self.manifest)
        manifest["jobs"][0]["pdfs"][0]["pdf"] = "../outside.pdf"
        with self.assertRaises(ValueError):
            self.render(manifest)

    def test_rejects_public_output_and_unsafe_progress_key(self):
        with patch("application_bot.manual_kit.PRIVATE_ROOT", self.root / "private"):
            with self.assertRaises(ValueError):
                self.render()
        with self.assertRaises(ValueError):
            self.render(key='"><script>bad</script>')

    def test_copies_supporting_materials_without_changing_hashes(self):
        folder = self.source / "Supporting_Documents"
        folder.mkdir()
        (folder / "Example_Unofficial_Transcript.pdf").write_bytes(b"synthetic supporting document")
        self.manifest["supporting_documents"] = [{"filename": "Example_Unofficial_Transcript.pdf",
            "sha256": hashlib.sha256(b"synthetic supporting document").hexdigest()}]
        self.render()
        self.assertEqual((self.output / "Supporting_Documents/Example_Unofficial_Transcript.pdf").read_bytes(),
                         b"synthetic supporting document")

    def test_new_conversation_has_campaign_and_progress_provenance(self):
        self.render()
        handoff = (self.output / "AGENT_HANDOFF.md").read_text(encoding='utf-8')
        self.assertIn(str(self.output), handoff)
        self.assertIn("example-campaign-", handoff)
        self.assertIn(hashlib.sha256((self.output / "Manifest.json").read_bytes()).hexdigest(), handoff)
        self.assertIn("实际手动进度未知", handoff)
        self.assertIn("官方回执证据或候选人明确确认", handoff)
        self.assertIn("保留原始排名和顺序", handoff)
        index = (self.output / "index.html").read_text(encoding='utf-8')
        self.assertIn('href="AGENT_HANDOFF.md"', index)
        self.assertIn('data-copy="handoff-prompt"', index)


if __name__ == "__main__":
    unittest.main()

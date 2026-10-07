from __future__ import annotations

import unittest

from cv.bot.bot import (
    FULL_TIME_GRADUATION_DATE,
    ascii_company_label,
    ascii_role_label,
    render_report,
    render_tailored_resume,
    select_evidence,
)


PROFILE = {
    "identity": {
        "display_name": "Alex Example",
        "headline_name": "Alex Example",
        "email": "alex@example.test",
    },
    "candidate_summary": "I am an engineering student focused on digital design.",
    "closing_strength": "I bring reviewed RTL and verification experience.",
    "graduation_school": "Example University",
    "tailored_summaries": {
        "CPU and computer architecture": "Reviewed CPU architecture summary.",
        "RTL verification": "Reviewed verification summary.",
        "default": "Reviewed hardware summary.",
    },
    "evidence_groups": [
        {
            "name": "CPU and computer architecture",
            "keywords": ["SystemVerilog", "RISC-V", "CPU", "microarchitecture", "Cocotb"],
            "evidence": "I implemented a reviewed processor project and its tests.",
        },
        {
            "name": "RTL verification",
            "keywords": ["RTL", "verification"],
            "evidence": "I developed reviewed RTL verification exercises.",
        },
    ],
}


class EvidenceMatchingTests(unittest.TestCase):
    def test_digital_cpu_role_prioritizes_rtl_evidence(self) -> None:
        matches = select_evidence(
            PROFILE,
            "Design SystemVerilog RTL for a RISC-V CPU microarchitecture and build Cocotb verification.",
        )
        self.assertEqual(matches[0].name, "CPU and computer architecture")
        self.assertGreaterEqual(matches[0].score, 3)

    def test_report_uses_only_profile_evidence(self) -> None:
        jd = "We seek an RTL design intern with verification experience."
        matches = select_evidence(PROFILE, jd)
        report = render_report(PROFILE, "Example Semiconductor", "RTL Intern", jd, matches, "current.tex")
        selected_evidence = {group["evidence"] for group in PROFILE["evidence_groups"]}
        paragraphs = [evidence for evidence in selected_evidence if evidence in report]
        self.assertTrue(paragraphs)
        self.assertNotIn("tape-out", report.casefold())
        self.assertIn("必须由人工审阅", report)

    def test_chinese_role_gets_ascii_cover_letter_label(self) -> None:
        matches = select_evidence(PROFILE, "digital IC RTL verification")
        role = ascii_role_label("数字IC验证工程师", matches)
        company = ascii_company_label("紫光展锐")
        self.assertTrue(role.isascii())
        self.assertEqual(company, "UNISOC")

    def test_full_time_variant_uses_june_2027_graduation(self) -> None:
        source = r"""\begin{rSection}{Education}
{\bf Example University} \hfill {\em Jan 2026 -- Dec 2027 (Expected)} \\
\end{rSection}
\begin{rSection}{Summary}
Old summary.
\end{rSection}
"""
        matches = select_evidence(PROFILE, "digital RTL design")
        rendered = render_tailored_resume(
            PROFILE,
            source,
            "Digital Design Engineer",
            matches,
            graduation_date=FULL_TIME_GRADUATION_DATE,
        )
        self.assertIn("Jan 2026 -- Jun 2027 (Expected)", rendered)
        self.assertNotIn("Dec 2027 (Expected)", rendered)

    def test_internship_variant_preserves_source_graduation(self) -> None:
        source = r"""\begin{rSection}{Education}
{\bf Example University} \hfill {\em Jan 2026 -- Dec 2027 (Expected)} \\
\end{rSection}
\begin{rSection}{Summary}
Old summary.
\end{rSection}
"""
        matches = select_evidence(PROFILE, "digital RTL intern")
        rendered = render_tailored_resume(PROFILE, source, "RTL Intern", matches)
        self.assertIn("Dec 2027 (Expected)", rendered)

    def test_confirmed_graduation_applies_to_internship_and_full_time(self) -> None:
        source = r"""\begin{rSection}{Education}
{\bf Example University} \hfill {\em Jan 2026 -- Dec 2027 (Expected)} \\
{\bf Other University} \hfill {\em Aug 2020 -- Jul 2025} \\
\end{rSection}
\begin{rSection}{Summary}
Old summary.
\end{rSection}
"""
        profile = {**PROFILE, "expected_graduation_date": "Jun 2027 (Expected)"}
        for role in ("RTL Intern", "RTL Engineer"):
            with self.subTest(role=role):
                rendered = render_tailored_resume(profile, source, role, [])
                self.assertIn("Jan 2026 -- Jun 2027 (Expected)", rendered)
                self.assertNotIn("Dec 2027", rendered)
                self.assertIn("Aug 2020 -- Jul 2025", rendered)


if __name__ == "__main__":
    unittest.main()

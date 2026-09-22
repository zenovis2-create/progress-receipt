from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from progress_receipt import accept, collect, render, summary
from progress_receipt._skill_loader import skill_root


class PackageLayoutTests(unittest.TestCase):
    def test_wrappers_load_the_canonical_standalone_skill_scripts(self) -> None:
        root = skill_root()
        implementations = {
            "collect_progress.py": collect._IMPLEMENTATION,
            "render_progress.py": render._IMPLEMENTATION,
            "accept_progress.py": accept._IMPLEMENTATION,
            "summary_progress.py": summary._IMPLEMENTATION,
        }
        for filename, module in implementations.items():
            with self.subTest(filename=filename):
                expected = (root / "scripts" / filename).resolve()
                self.assertEqual(expected, Path(module.__file__).resolve())
                self.assertTrue(expected.is_file())

    def test_standalone_skill_contains_its_contract_template_and_agent_metadata(self) -> None:
        root = skill_root()
        self.assertTrue((root / "SKILL.md").is_file())
        self.assertTrue((root / "references" / "report-contract.md").is_file())
        self.assertTrue((root / "assets" / "report-template.html").is_file())
        self.assertTrue((root / "agents" / "openai.yaml").is_file())


class CommittedSelfReportTests(unittest.TestCase):
    """The published example must keep matching its own receipt.

    Line-ending translation, a stray edit, or a partial regeneration would all
    break the integrity claim the README makes about this directory.
    """

    def report(self) -> Path:
        return Path(__file__).resolve().parents[1] / "examples" / "self-report"

    def test_committed_self_report_matches_its_integrity_receipt(self) -> None:
        report = self.report()
        if not report.is_dir():
            self.skipTest("the self-report example is not present in this distribution")
        integrity = json.loads((report / "integrity.json").read_text(encoding="utf-8"))

        self.assertEqual(
            integrity["htmlSha256"], hashlib.sha256((report / "index.html").read_bytes()).hexdigest()
        )
        self.assertEqual(
            integrity["manifestSha256"], hashlib.sha256((report / "manifest.json").read_bytes()).hexdigest()
        )
        for relative, digest in integrity["assets"].items():
            with self.subTest(asset=relative):
                self.assertEqual(digest, hashlib.sha256((report / relative).read_bytes()).hexdigest())

    def test_committed_self_report_demonstrates_every_claim_state(self) -> None:
        report = self.report()
        if not report.is_dir():
            self.skipTest("the self-report example is not present in this distribution")
        manifest = json.loads((report / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(
            {"changed", "verified", "blocked", "not_observed"},
            {claim["status"] for claim in manifest["claims"]},
        )


if __name__ == "__main__":
    unittest.main()

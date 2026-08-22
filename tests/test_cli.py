from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import shutil
import unittest

from progress_receipt import cli


class DemoCommandTests(unittest.TestCase):
    def test_demo_generates_a_complete_privacy_safe_report(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            result = cli.main(["demo"])
        index = Path(output.getvalue().strip())
        workspace = index.parent.parent
        self.addCleanup(shutil.rmtree, workspace, True)

        self.assertEqual(0, result)
        self.assertTrue(index.is_file())
        self.assertTrue((index.parent / "integrity.json").is_file())
        self.assertTrue((workspace / "state.json").is_file())
        manifest = json.loads((index.parent / "manifest.json").read_text(encoding="utf-8"))
        statuses = {claim["status"] for claim in manifest["claims"]}
        self.assertEqual({"verified", "changed", "blocked", "not_observed"}, statuses)
        verified = next(claim for claim in manifest["claims"] if claim["status"] == "verified")
        linked = {item["id"]: item for item in manifest["evidence"]}[verified["evidenceIds"][0]]
        self.assertEqual("command", linked["kind"])
        self.assertEqual(0, linked["exitCode"])
        self.assertEqual(1, len(manifest["report"]["visualComparisons"]))
        self.assertIn("agent-authored", index.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

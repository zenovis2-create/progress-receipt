from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from progress_receipt import cli
from progress_receipt._demo import _create_fixture, _isolated_git_env


class DemoCommandTests(unittest.TestCase):
    def run_demo(self, *arguments: str) -> tuple[int, str, str]:
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            result = cli.main(["demo", *arguments])
        return result, out.getvalue(), err.getvalue()

    def test_demo_generates_a_complete_privacy_safe_report(self) -> None:
        result, out, err = self.run_demo()
        index = Path(out.strip())
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

    def test_demo_summary_goes_to_stderr_and_the_path_stays_parseable(self) -> None:
        result, out, err = self.run_demo()
        index = Path(out.strip())
        self.addCleanup(shutil.rmtree, index.parent.parent, True)

        self.assertEqual(0, result)
        self.assertEqual(index, index.resolve())
        self.assertIn("1 verified · 1 changed · 1 blocked · 1 not_observed", err)
        self.assertIn("no network", err)

    def test_demo_quiet_prints_only_the_report_path(self) -> None:
        result, out, err = self.run_demo("--quiet")
        index = Path(out.strip())
        self.addCleanup(shutil.rmtree, index.parent.parent, True)

        self.assertEqual(0, result)
        self.assertTrue(index.is_file())
        self.assertEqual("", err)

    def test_demo_output_directory_is_self_contained_and_leaves_no_scratch(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        destination = Path(temp.name) / "receipt"

        result, out, _ = self.run_demo("--output", str(destination))

        self.assertEqual(0, result)
        self.assertEqual(destination / "index.html", Path(out.strip()))
        self.assertEqual(
            {"index.html", "manifest.json", "integrity.json", "assets"},
            {entry.name for entry in destination.iterdir()},
        )

    def test_demo_refuses_to_overwrite_an_existing_output(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        destination = Path(temp.name) / "receipt"
        destination.mkdir()

        with self.assertRaises(SystemExit):
            self.run_demo("--output", str(destination))


class DemoFixtureTests(unittest.TestCase):
    def test_fixture_files_are_committed_with_identical_bytes_on_every_platform(self) -> None:
        """The demo's one verified command is `git diff --check`.

        If the fixture were written with native line endings it would report
        trailing whitespace on Windows unless the user happened to have
        core.autocrlf enabled, and the verified claim would be rejected.
        """
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name) / "fixture"
        base, head = _create_fixture(repo)

        check = subprocess.run(
            ["git", "diff", "--check", f"{base}..{head}"],
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_isolated_git_env(),
        )
        blob = subprocess.run(
            ["git", "show", f"{head}:receipt.txt"],
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_isolated_git_env(),
        )

        self.assertEqual(0, check.returncode, check.stdout.decode("utf-8", "replace"))
        self.assertNotIn(b"\r", blob.stdout)


if __name__ == "__main__":
    unittest.main()

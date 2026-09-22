from __future__ import annotations

import copy
from contextlib import redirect_stdout
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from progress_receipt import accept, cli, render, summary
from progress_receipt._demo import build_demo
from progress_receipt._skill_loader import skill_root
from test_render_progress import base_manifest, capture, PNG_1X1, SHA_A, SHA_B


class SummaryProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.report = self.root / "report"
        self.output = self.root / "summary.md"

    def publish(self, payload: dict | None = None) -> None:
        draft = self.root / "draft.json"
        draft.write_text(json.dumps(payload if payload is not None else base_manifest()), encoding="utf-8")
        (self.root / "capture.png").write_bytes(PNG_1X1)
        render.publish(draft, skill_root() / "assets" / "report-template.html", self.report)

    def export(self, **kwargs) -> str:
        return summary.export_summary(self.report, "report/index.html", **kwargs)

    def reseal(self, payload: dict | None = None) -> None:
        if payload is not None:
            (self.report / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
        receipt_path = self.report / "integrity.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        for name, key in (("index.html", "htmlSha256"), ("manifest.json", "manifestSha256")):
            receipt[key] = hashlib.sha256((self.report / name).read_bytes()).hexdigest()
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    def test_all_outcomes_provenance_refs_and_evidence_links(self) -> None:
        payload = base_manifest()
        for status in ("changed", "not_observed", "blocked"):
            payload["claims"].append({
                "id": status, "title": status, "detail": "Observed detail", "source": "human",
                "status": status, "evidenceIds": ["test"],
            })
        self.publish(payload)
        result = self.export()
        for status in summary.STATUS_ORDER:
            self.assertIn(f"`{status}`: 1", result)
        positions = [result.index(f"- **{status}**") for status in summary.STATUS_ORDER]
        self.assertEqual(sorted(positions), positions)
        self.assertIn("source: `human`", result)
        self.assertIn("[test](<report/index.html#evidence-test>)", result)
        self.assertIn(SHA_A, result)
        self.assertIn(SHA_B, result)
        self.assertIn("Recorded evidence package: **verified**", result)
        self.assertIn("Release readiness is not assessed", result)
        self.assertIn("acceptance were not rechecked", result)
        self.assertIn("not verified", result)
        self.assertIn(hashlib.sha256((self.report / "manifest.json").read_bytes()).hexdigest(), result)

    def test_omissions_and_text_are_bounded_without_hiding_gap_counts(self) -> None:
        payload = base_manifest()
        for i in range(12):
            payload["claims"].append({
                "id": f"blocked-{i}", "title": "t" * 1000, "detail": "d" * 1000,
                "source": "agent", "status": "blocked", "evidenceIds": ["test"] * 7,
            })
        self.publish(payload)
        result = self.export()
        self.assertEqual(8, result.count("- **blocked**"))
        self.assertNotIn("- **verified**", result)
        self.assertIn("`blocked`: 12", result)
        self.assertIn("Omitted claims: `blocked`: 4 · `not_observed`: 0 · `changed`: 0 · `verified`: 1", result)
        self.assertIn("+4 links omitted", result)
        self.assertIn("t" * 119 + "…", result)
        self.assertNotIn("d" * 240, result)
        self.assertLess(len(result), 7000)

    def test_empty_incomplete_and_korean_reports_do_not_imply_checks(self) -> None:
        payload = base_manifest()
        payload["report"].update(lang="ko", status="incomplete")
        payload["claims"] = []
        payload["evidence"] = []
        payload["qualityGate"]["status"] = "incomplete"
        self.publish(payload)
        result = self.export()
        self.assertIn("프로젝트 증거 요약", result)
        self.assertIn("기록된 증거 패키지: **incomplete**", result)
        self.assertIn("기록된 주장이 없습니다", result)
        self.assertIn("`verified`: 0", result)
        self.assertIn("배포 준비 여부는 판정하지 않습니다", result)

    def test_narrative_is_literal_single_line_sanitized_markdown(self) -> None:
        payload = base_manifest()
        payload["claims"][0]["title"] = '<script>alert(1)</script> ![x](https://evil.invalid) @team'
        payload["claims"][0]["detail"] = 'line1\n## fake heading\n[x](javascript:alert(1)) `code` | token=supersecret'
        self.publish(payload)
        result = self.export()
        self.assertNotIn("<script>", result)
        self.assertNotIn("![x]", result)
        self.assertNotIn("\n## fake", result)
        self.assertNotIn("@team", result)
        self.assertNotIn("supersecret", result)
        self.assertIn("&#60;script&#62;", result)
        self.assertIn("&#91;x&#93;", result)

    def test_bad_destinations_are_rejected_before_any_output(self) -> None:
        for url in ("", "javascript:alert(1)", "data:text/html,x", "file:///tmp/index.html", "//evil.invalid/r", "/absolute/index.html", "C:/report.html", "https://a.invalid/?token=secret", "https://a.invalid/#old", "https://u:p@a.invalid/r", "https://a.invalid/\nnew", "report\\index.html", "https://[broken", "https:///missing"):
            with self.subTest(url=url), self.assertRaises(summary.SummaryError):
                summary.export_summary(self.report, url, self.output)
        self.assertFalse(self.output.exists())

    def test_explicit_https_and_relative_destinations_are_encoded(self) -> None:
        self.publish()
        for url in ("https://example.invalid/report(1)/index.html", "../report(1)/index.html"):
            with self.subTest(url=url):
                result = summary.export_summary(self.report, url)
                self.assertIn(url.replace("(", "%28").replace(")", "%29") + "#evidence-test", result)

    def test_old_html_without_anchors_falls_back_to_report(self) -> None:
        self.publish()
        html = self.report / "index.html"
        html.write_text(html.read_text(encoding="utf-8").replace('id="evidence-test"', 'id="legacy-test"'), encoding="utf-8")
        self.reseal()
        result = self.export()
        self.assertIn("[test](<report/index.html>)", result)
        self.assertNotIn("#evidence-test", result)
        self.assertIn("Some HTML evidence anchors are absent", result)

    def test_export_preserves_package_bytes_and_never_executes_commands(self) -> None:
        self.publish()
        before = {p.relative_to(self.report): p.read_bytes() for p in self.report.rglob("*") if p.is_file()}
        with patch("subprocess.run", side_effect=AssertionError("No replay or Git execution")):
            result = self.export(output=self.output)
        self.assertEqual(result, self.output.read_text(encoding="utf-8"))
        self.assertEqual(before, {p.relative_to(self.report): p.read_bytes() for p in self.report.rglob("*") if p.is_file()})
        accept.verify_integrity(self.report)

    def test_output_inside_report_or_existing_file_is_rejected(self) -> None:
        self.publish()
        for destination in (self.report / "summary.md", self.report / "assets" / "summary.md", self.report / "index.html"):
            with self.subTest(path=destination), self.assertRaisesRegex(summary.SummaryError, "outside"):
                self.export(output=destination)
        self.output.write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(summary.SummaryError, "already exists"):
            self.export(output=self.output)
        self.assertEqual("keep", self.output.read_text(encoding="utf-8"))
        accept.verify_integrity(self.report)

    def test_symlink_into_report_is_rejected(self) -> None:
        self.publish()
        alias = self.root / "alias"
        try:
            alias.symlink_to(self.report, target_is_directory=True)
        except OSError:
            self.skipTest("creating symlinks is not permitted")
        with self.assertRaisesRegex(summary.SummaryError, "outside"):
            self.export(output=alias / "summary.md")
        accept.verify_integrity(self.report)

    def test_tampered_html_manifest_capture_and_extra_files_fail_closed(self) -> None:
        payload = base_manifest()
        payload["evidence"].append(capture("shot", "capture.png"))
        self.publish(payload)
        for file in (self.report / "index.html", self.report / "manifest.json", next((self.report / "assets").iterdir())):
            original = file.read_bytes()
            with self.subTest(file=file):
                file.write_bytes(original + b"tampered")
                with self.assertRaises(accept.AcceptError):
                    self.export(output=self.output)
                self.assertFalse(self.output.exists())
                file.write_bytes(original)
        (self.report / "extra.txt").write_text("unsealed", encoding="utf-8")
        with self.assertRaises(accept.AcceptError):
            self.export(output=self.output)
        self.assertFalse(self.output.exists())

    def test_resealed_invalid_claims_cannot_bypass_manifest_validation(self) -> None:
        self.publish()
        original = json.loads((self.report / "manifest.json").read_text(encoding="utf-8"))
        for change in ({"exitCode": 1}, {"producedAtRef": SHA_A}, {"worktreeFingerprint": "c" * 64}, {"historical": True}):
            with self.subTest(change=change):
                payload = copy.deepcopy(original)
                payload["evidence"][0].update(change)
                self.reseal(payload)
                with self.assertRaises(render.RenderError):
                    self.export(output=self.output)
                self.assertFalse(self.output.exists())

    def test_resealed_capture_hash_mismatch_is_rejected(self) -> None:
        payload = base_manifest()
        payload["evidence"].append(capture("shot", "capture.png"))
        self.publish(payload)
        published = json.loads((self.report / "manifest.json").read_text(encoding="utf-8"))
        published["evidence"][1]["sha256"] = "0" * 64
        self.reseal(published)
        with self.assertRaisesRegex(accept.AcceptError, "capture evidence"):
            self.export(output=self.output)

    def test_cli_stdout_and_standalone_script_match(self) -> None:
        self.publish()
        arguments = ["--report", str(self.report), "--report-url", "report/index.html"]
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(0, cli.main(["summary", *arguments]))
        standalone = subprocess.run([sys.executable, str(skill_root() / "scripts" / "summary_progress.py"), *arguments], capture_output=True, text=True, encoding="utf-8", check=True, env={**os.environ, "PYTHONIOENCODING": "ascii"})
        self.assertEqual(out.getvalue(), standalone.stdout)
        self.assertEqual("", standalone.stderr)
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(0, cli.main(["summary", *arguments, "--output", str(self.output)]))
        self.assertEqual("", out.getvalue())
        self.assertEqual(standalone.stdout, self.output.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(SystemExit, "progress summary failed"):
            cli.main(["summary", *arguments, "--output", str(self.output)])

    def test_demo_baseline_and_acceptance_survive_export(self) -> None:
        index, _ = build_demo()
        workspace = index.parent.parent
        self.addCleanup(shutil.rmtree, workspace, True)
        state = workspace / "state.json"
        before = state.read_bytes()
        summary.export_summary(index.parent, "report/index.html", workspace / "pr.md")
        self.assertEqual(before, state.read_bytes())
        accept.accept(workspace / "fixture", index.parent, state, None, reset_baseline=True)


if __name__ == "__main__":
    unittest.main()

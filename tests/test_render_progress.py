from __future__ import annotations

import base64
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from progress_receipt import render as render_progress
from progress_receipt._skill_loader import skill_root


SHA_A = "a" * 40
SHA_B = "b" * 40
PNG_1X1 = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")


def base_manifest() -> dict:
    return {
        "schemaVersion": 1,
        "repository": {"id": "repo", "branch": "main", "worktreeFingerprint": "clean", "source": "git"},
        "range": {"fromRef": SHA_A, "toRef": SHA_B},
        "report": {
            "title": "Progress",
            "lang": "en",
            "status": "verified",
            "outcome": {"text": "Outcome", "source": "agent"},
            "before": {"text": "Before", "source": "agent"},
            "after": {"text": "After", "source": "agent"},
        },
        "claims": [{"id": "claim", "title": "Claim", "detail": "Detail", "status": "verified", "source": "agent", "evidenceIds": ["test"]}],
        "evidence": [{"id": "test", "kind": "command", "label": "Test", "source": "tool", "producedAtRef": SHA_B, "worktreeFingerprint": "clean", "command": "test", "exitCode": 0, "capturedAt": "2026-08-04T00:00:00Z"}],
        "qualityGate": {"status": "passed", "browserQa": {"completed": True, "source": "agent", "viewports": ["desktop"]}},
        "inventory": {"files": [], "commits": [], "totalFiles": 0, "totalCommits": 0, "linesAdded": 0, "linesDeleted": 0},
        "collection": {"source": "tool", "maxFiles": 100, "maxCommits": 100},
        "privacy": {"textSanitized": True, "capturesReviewed": False},
    }


def capture(evidence_id: str, path: str, historical: bool = False) -> dict:
    result = {
        "id": evidence_id,
        "kind": "capture",
        "label": evidence_id,
        "source": "tool",
        "producedAtRef": SHA_A if historical else SHA_B,
        "worktreeFingerprint": "unknown" if historical else "clean",
        "capturedAt": "2026-08-04T00:00:00Z",
        "path": path,
        "reviewed": True,
        "reviewer": {"source": "human", "name": "Reviewer"},
        "alt": f"{evidence_id} capture",
    }
    if historical:
        result["historical"] = True
    return result


class RenderProgressTests(unittest.TestCase):
    def publish(self, manifest: dict) -> tuple[Path, str, tempfile.TemporaryDirectory]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        for name in ("before.png", "after.png", "map-before.png", "map-after.png"):
            (root / name).write_bytes(PNG_1X1)
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        output = root / "report"
        template = skill_root() / "assets" / "report-template.html"
        render_progress.publish(manifest_path, template, output)
        return output, (output / "index.html").read_text(encoding="utf-8"), temp

    def test_legacy_schema_v1_manifest_still_renders(self) -> None:
        output, html, temp = self.publish(base_manifest())
        self.addCleanup(temp.cleanup)
        self.assertTrue((output / "integrity.json").is_file())
        self.assertNotIn("Visual before and after", html)
        self.assertNotIn("{{", html)

    def test_multiple_visual_comparisons_render_accessible_escaped_controls(self) -> None:
        manifest = base_manifest()
        manifest["report"]["scope"] = {"text": "HUD <script>alert(1)</script>", "source": "agent"}
        manifest["report"]["highlights"] = [{"text": "Readable & fast", "source": "human", "status": "verified"}]
        manifest["evidence"].extend([
            capture("hud-before", "before.png", historical=True),
            capture("hud-after", "after.png"),
            capture("map-before", "map-before.png", historical=True),
            capture("map-after", "map-after.png"),
        ])
        manifest["report"]["visualComparisons"] = [
            {"id": "hud", "title": "Battle HUD", "detail": "HUD states", "source": "agent", "status": "changed", "beforeEvidenceId": "hud-before", "afterEvidenceId": "hud-after"},
            {"id": "map", "title": "Campaign map", "layout": "portrait", "source": "human", "status": "verified", "beforeEvidenceId": "map-before", "afterEvidenceId": "map-after"},
        ]
        output, html, temp = self.publish(manifest)
        self.addCleanup(temp.cleanup)
        self.assertEqual(2, html.count('class="comparison-tab"'))
        self.assertEqual(2, html.count('type="range"'))
        self.assertIn('role="tablist"', html)
        self.assertIn('aria-controls="comparison-hud"', html)
        self.assertIn('class="comparison-stage layout-landscape"', html)
        self.assertIn('class="comparison-stage layout-portrait"', html)
        self.assertIn("clip-path:inset(0 0 0 var(--split))", html)
        self.assertIn("HUD &lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("HUD <script>", html)
        self.assertNotIn("https://", html)
        published = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("unknown", next(item for item in published["evidence"] if item["id"] == "hud-before")["worktreeFingerprint"])
        artifact_dir = os.environ.get("PROGRESS_TEST_ARTIFACT_DIR")
        if artifact_dir:
            shutil.copytree(output, Path(artifact_dir))

    def test_all_report_states_use_a_neutral_heading(self) -> None:
        for language in ("en", "ko"):
            for status in ("incomplete", "blocked", "verified"):
                with self.subTest(language=language, status=status):
                    manifest = base_manifest()
                    manifest["report"]["lang"] = language
                    manifest["report"]["status"] = status
                    manifest["claims"][0]["status"] = "changed" if status == "incomplete" else status
                    _, html, temp = self.publish(manifest)
                    self.addCleanup(temp.cleanup)
                    self.assertNotIn("Verified project progress", html)
                    self.assertNotIn("검증된 프로젝트 진행", html)
                    self.assertNotIn("Accepted baseline", html)
                    self.assertIn("Project evidence receipt" if language == "en" else "프로젝트 증거 보고서", html)
                    self.assertIn(f'Evidence package: {status}' if language == "en" else f'증거 패키지: {status}', html)

    def test_mixed_outcomes_are_counted_and_gaps_shown_first(self) -> None:
        manifest = base_manifest()
        for status in ("changed", "not_observed", "blocked"):
            manifest["claims"].append({
                "id": status, "title": status, "detail": status,
                "status": status, "source": "agent", "evidenceIds": ["test"],
            })
        output, html, temp = self.publish(manifest)
        self.addCleanup(temp.cleanup)
        for status in ("blocked", "not_observed", "changed", "verified"):
            self.assertIn(f'{status}: 1', html)
        self.assertIn("Evidence package: verified", html)
        self.assertIn("Release readiness is not assessed", html)
        order = [html.index(f'id="claim-{name}"') for name in ("blocked", "not_observed", "changed", "claim")]
        self.assertEqual(sorted(order), order)
        self.assertLess(order[-1], html.index('class="metrics"'))
        published = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["claims"], published["claims"])

    def test_empty_report_does_not_imply_verified_outcomes(self) -> None:
        manifest = base_manifest()
        manifest["report"]["status"] = "incomplete"
        manifest["claims"] = []
        manifest["evidence"] = []
        _, html, temp = self.publish(manifest)
        self.addCleanup(temp.cleanup)
        for status in ("blocked", "not_observed", "changed", "verified"):
            self.assertIn(f'{status}: 0', html)
        self.assertIn("No claims were recorded", html)

    def test_claim_links_target_unique_focusable_evidence(self) -> None:
        manifest = base_manifest()
        manifest["claims"][0]["id"] = "test"
        manifest["evidence"][0]["label"] = 'Check <script>alert("x")</script>'
        _, html, temp = self.publish(manifest)
        self.addCleanup(temp.cleanup)
        self.assertIn('href="#evidence-test">test</a>', html)
        self.assertEqual(1, html.count('id="evidence-test"'))
        self.assertEqual(1, html.count('id="claim-test"'))
        self.assertIn('id="evidence-test" class="evidence command" tabindex="-1"', html)
        self.assertIn("Check &lt;script&gt;", html)
        self.assertNotIn('Check <script>', html)
        self.assertIn('captured: 2026-08-04T00:00:00Z', html)

    def test_evidence_fragment_rejects_unsafe_ids(self) -> None:
        for value in ('x\" onclick=\"alert(1)', '../test', 'https://example.com', 'x#other'):
            with self.subTest(value=value):
                manifest = base_manifest()
                manifest["evidence"][0]["id"] = value
                manifest["claims"][0]["evidenceIds"] = [value]
                with tempfile.TemporaryDirectory() as temp:
                    with self.assertRaisesRegex(render_progress.RenderError, "safe identifiers"):
                        render_progress.validate_manifest(manifest, Path(temp) / "manifest.json")

    def test_capture_discloses_escaped_reviewer_identity(self) -> None:
        manifest = base_manifest()
        item = capture("shot", "before.png")
        item["reviewer"]["name"] = "Reviewer <admin>"
        manifest["evidence"].append(item)
        _, html, temp = self.publish(manifest)
        self.addCleanup(temp.cleanup)
        self.assertIn("reviewed by: Reviewer &lt;admin&gt; (human)", html)

    def test_verified_claim_still_rejects_failed_commands(self) -> None:
        manifest = base_manifest()
        manifest["evidence"][0]["exitCode"] = 1
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(render_progress.RenderError, "failed command"):
                render_progress.validate_manifest(manifest, Path(temp) / "manifest.json")

    def test_comparison_rejects_non_capture_evidence(self) -> None:
        manifest = base_manifest()
        manifest["report"]["visualComparisons"] = [{"id": "bad", "title": "Bad", "source": "agent", "status": "changed", "beforeEvidenceId": "test", "afterEvidenceId": "test"}]
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(render_progress.RenderError, "reviewed capture evidence"):
                render_progress.validate_manifest(manifest, Path(temp) / "manifest.json")

    def test_unknown_fingerprint_requires_historical_true(self) -> None:
        manifest = base_manifest()
        bad = copy.deepcopy(manifest["evidence"][0])
        bad["id"] = "unknown"
        bad["worktreeFingerprint"] = "unknown"
        manifest["evidence"].append(bad)
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(render_progress.RenderError, "only when historical"):
                render_progress.validate_manifest(manifest, Path(temp) / "manifest.json")

    def test_comparison_rejects_unknown_layout(self) -> None:
        manifest = base_manifest()
        manifest["evidence"].extend([capture("before", "before.png"), capture("after", "after.png")])
        manifest["report"]["visualComparisons"] = [{"id": "bad-layout", "title": "Bad layout", "layout": "square", "source": "agent", "status": "changed", "beforeEvidenceId": "before", "afterEvidenceId": "after"}]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "before.png").write_bytes(PNG_1X1)
            (root / "after.png").write_bytes(PNG_1X1)
            with self.assertRaisesRegex(render_progress.RenderError, "layout is invalid"):
                render_progress.validate_manifest(manifest, root / "manifest.json")

    def test_verified_claim_rejects_stale_evidence(self) -> None:
        manifest = base_manifest()
        manifest["evidence"][0]["producedAtRef"] = SHA_A
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(render_progress.RenderError, "uses stale evidence"):
                render_progress.validate_manifest(manifest, Path(temp) / "manifest.json")

    def test_verified_claim_rejects_changed_worktree_fingerprint(self) -> None:
        manifest = base_manifest()
        manifest["repository"]["worktreeFingerprint"] = "1" * 64
        manifest["evidence"][0]["worktreeFingerprint"] = "2" * 64
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(render_progress.RenderError, "different worktree"):
                render_progress.validate_manifest(manifest, Path(temp) / "manifest.json")

    def test_capture_rejects_parent_path_traversal(self) -> None:
        manifest = base_manifest()
        manifest["evidence"].append(capture("outside", "../outside.png"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest_dir = root / "manifest"
            manifest_dir.mkdir()
            (root / "outside.png").write_bytes(PNG_1X1)
            with self.assertRaisesRegex(render_progress.RenderError, "local relative image path"):
                render_progress.validate_manifest(manifest, manifest_dir / "manifest.json")

    def test_capture_rejects_symlink_that_escapes_manifest_directory(self) -> None:
        manifest = base_manifest()
        manifest["evidence"].append(capture("linked", "linked.png"))
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp)
            target = Path(outside) / "outside.png"
            target.write_bytes(PNG_1X1)
            try:
                (root / "linked.png").symlink_to(target)
            except OSError as exc:
                if os.name != "nt":
                    self.skipTest(f"file symlinks are unavailable: {exc}")
                junction = root / "linked"
                subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(junction), str(Path(outside))],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                manifest["evidence"][-1]["path"] = "linked/outside.png"
            with self.assertRaisesRegex(render_progress.RenderError, "escapes the manifest directory"):
                render_progress.validate_manifest(manifest, root / "manifest.json")

    def test_capture_rejects_windows_style_and_rooted_paths(self) -> None:
        for raw in ("assets\\before.png", "C:before.png", "C:/data/before.png", "/etc/before.png", "before.png:stream"):
            with self.subTest(raw=raw):
                manifest = base_manifest()
                manifest["evidence"].append(capture("odd", raw))
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    (root / "before.png").write_bytes(PNG_1X1)
                    with self.assertRaises(render_progress.RenderError):
                        render_progress.validate_manifest(manifest, root / "manifest.json")

    def test_capture_accepts_a_relative_subdirectory_path(self) -> None:
        manifest = base_manifest()
        manifest["evidence"].append(capture("nested", "shots/before.png"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "shots").mkdir()
            (root / "shots" / "before.png").write_bytes(PNG_1X1)
            captures = render_progress.validate_manifest(manifest, root / "manifest.json")
            self.assertEqual((root / "shots" / "before.png").resolve(), captures["nested"])

    def test_capture_rejects_a_file_that_is_not_an_image(self) -> None:
        manifest = base_manifest()
        manifest["evidence"].append(capture("fake", "fake.png"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "fake.png").write_bytes(b"<html>not an image</html>")
            with self.assertRaisesRegex(render_progress.RenderError, "not a PNG, JPEG, or WebP"):
                render_progress.validate_manifest(manifest, root / "manifest.json")

    def test_capture_rejects_an_extension_that_contradicts_the_bytes(self) -> None:
        manifest = base_manifest()
        manifest["evidence"].append(capture("mismatch", "shot.jpg"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "shot.jpg").write_bytes(PNG_1X1)
            with self.assertRaisesRegex(render_progress.RenderError, "does not match its image format"):
                render_progress.validate_manifest(manifest, root / "manifest.json")

    def test_render_discloses_bounded_inventory_truncation(self) -> None:
        manifest = base_manifest()
        manifest["inventory"] = {
            "files": [{"status": "M", "path": "shown.txt", "added": 1, "deleted": 0, "binary": False, "source": "git"}],
            "commits": [],
            "totalFiles": 3,
            "totalCommits": 0,
            "linesAdded": 3,
            "linesDeleted": 0,
        }

        _, html, temp = self.publish(manifest)
        self.addCleanup(temp.cleanup)

        self.assertIn("showing 1 of 3 files", html)
        self.assertIn("2 omitted", html)


if __name__ == "__main__":
    unittest.main()

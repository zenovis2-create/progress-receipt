from __future__ import annotations

import base64
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

import render_progress


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
        template = Path(render_progress.__file__).parent.parent / "assets" / "report-template.html"
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
        self.assertIn("HUD &lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("HUD <script>", html)
        self.assertNotIn("https://", html)
        published = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("unknown", next(item for item in published["evidence"] if item["id"] == "hud-before")["worktreeFingerprint"])
        artifact_dir = os.environ.get("PROGRESS_TEST_ARTIFACT_DIR")
        if artifact_dir:
            shutil.copytree(output, Path(artifact_dir))

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


if __name__ == "__main__":
    unittest.main()

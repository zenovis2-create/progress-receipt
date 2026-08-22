from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from progress_receipt import accept as accept_progress
from progress_receipt import collect as collect_progress
from progress_receipt import render as render_progress
from progress_receipt._skill_loader import skill_root


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.decode("utf-8").strip()


class AcceptanceTests(unittest.TestCase):
    def make_repo(self) -> tuple[Path, Path, str, str]:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        repo = root / "repo"
        repo.mkdir()
        git(repo, "init", "--quiet")
        git(repo, "branch", "-M", "main")
        git(repo, "config", "user.name", "Progress Test")
        git(repo, "config", "user.email", "progress@example.invalid")
        (repo / "status.txt").write_text("before\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "--quiet", "-m", "baseline")
        base = git(repo, "rev-parse", "HEAD")
        (repo / "status.txt").write_text("after\n", encoding="utf-8")
        git(repo, "commit", "--quiet", "-am", "verified change")
        head = git(repo, "rev-parse", "HEAD")
        return root, repo, base, head

    def build_report(
        self,
        root: Path,
        repo: Path,
        base: str,
        head: str,
        *,
        branch_key: str | None = None,
        capture: bool = False,
    ) -> tuple[dict, Path]:
        args = argparse.Namespace(
            repo=str(repo),
            base=base,
            head=head,
            state=None,
            branch_key=branch_key,
            max_files=200,
            max_commits=50,
        )
        manifest = collect_progress.build_manifest(args)
        fingerprint = manifest["repository"]["worktreeFingerprint"]
        manifest["report"] = {
            "title": "Acceptance fixture",
            "lang": "en",
            "status": "verified",
            "outcome": {"text": "The fixture passed its diff check.", "source": "agent"},
            "before": {"text": "The status was before.", "source": "agent"},
            "after": {"text": "The status is after.", "source": "agent"},
        }
        manifest["claims"] = [
            {
                "id": "diff-check",
                "title": "Diff check passed",
                "detail": "A current command is attached.",
                "status": "verified",
                "source": "agent",
                "evidenceIds": ["diff-check"],
            }
        ]
        manifest["evidence"] = [
            {
                "id": "diff-check",
                "kind": "command",
                "label": "Git diff check",
                "source": "tool",
                "producedAtRef": head,
                "worktreeFingerprint": fingerprint,
                "command": f"git diff --check {base}..{head}",
                "exitCode": 0,
                "capturedAt": "2026-08-22T00:00:00Z",
            }
        ]
        if capture:
            source = root / "capture.png"
            source.write_bytes(PNG_1X1)
            manifest["evidence"].append(
                {
                    "id": "capture",
                    "kind": "capture",
                    "label": "Reviewed capture",
                    "source": "tool",
                    "producedAtRef": head,
                    "worktreeFingerprint": fingerprint,
                    "capturedAt": "2026-08-22T00:00:00Z",
                    "path": source.name,
                    "reviewed": True,
                    "reviewer": {"source": "human", "name": "Test reviewer"},
                    "alt": "Reviewed fixture capture.",
                }
            )
        manifest["qualityGate"] = {
            "status": "passed",
            "browserQa": {"completed": True, "source": "agent", "viewports": ["desktop"]},
        }
        draft = root / "draft.json"
        draft.write_text(json.dumps(manifest), encoding="utf-8")
        report = root / "report"
        render_progress.publish(draft, skill_root() / "assets" / "report-template.html", report)
        return manifest, report

    def test_end_to_end_collect_enrich_render_accept_succeeds(self) -> None:
        root, repo, base, head = self.make_repo()
        manifest, report = self.build_report(root, repo, base, head)
        state_path = root / "state.json"

        accept_progress.accept(repo, report, state_path, None)

        state = json.loads(state_path.read_text(encoding="utf-8"))
        key = f'{manifest["repository"]["id"]}:main'
        self.assertEqual(head, state["branches"][key]["toRef"])

    def test_acceptance_rejects_baseline_divergence(self) -> None:
        root, repo, base, head = self.make_repo()
        manifest, report = self.build_report(root, repo, base, head)
        key = f'{manifest["repository"]["id"]}:main'
        state_path = root / "state.json"
        state_path.write_text(
            json.dumps({"schemaVersion": 1, "branches": {key: {"toRef": head}}}),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(accept_progress.AcceptError, "last accepted state"):
            accept_progress.accept(repo, report, state_path, None)

    def test_acceptance_handles_detached_head_with_explicit_branch_key(self) -> None:
        root, repo, base, head = self.make_repo()
        git(repo, "checkout", "--quiet", "--detach", head)
        _, report = self.build_report(root, repo, base, head, branch_key="detached-demo")
        state_path = root / "state.json"

        with self.assertRaisesRegex(collect_progress.ProgressError, "Detached HEAD"):
            accept_progress.accept(repo, report, state_path, None)

        accept_progress.accept(repo, report, state_path, "detached-demo")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertTrue(any(key.endswith(":detached-demo") for key in state["branches"]))

    def test_acceptance_rejects_tampered_rendered_report(self) -> None:
        root, repo, base, head = self.make_repo()
        _, report = self.build_report(root, repo, base, head)
        (report / "index.html").write_text("tampered", encoding="utf-8")

        with self.assertRaisesRegex(accept_progress.AcceptError, "integrity receipt"):
            accept_progress.accept(repo, report, root / "state.json", None)

    def test_acceptance_rejects_tampered_manifest(self) -> None:
        root, repo, base, head = self.make_repo()
        _, report = self.build_report(root, repo, base, head)
        manifest_path = report / "manifest.json"
        manifest_path.write_text(manifest_path.read_text(encoding="utf-8") + " ", encoding="utf-8")

        with self.assertRaisesRegex(accept_progress.AcceptError, "integrity receipt"):
            accept_progress.accept(repo, report, root / "state.json", None)

    def test_acceptance_rejects_tampered_capture(self) -> None:
        root, repo, base, head = self.make_repo()
        _, report = self.build_report(root, repo, base, head, capture=True)
        (report / "assets" / "capture.png").write_bytes(PNG_1X1 + b"tampered")

        with self.assertRaisesRegex(accept_progress.AcceptError, "assets do not match"):
            accept_progress.accept(repo, report, root / "state.json", None)


if __name__ == "__main__":
    unittest.main()

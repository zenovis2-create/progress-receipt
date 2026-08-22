"""Create the deterministic, network-free launch demo."""

# SPDX-License-Identifier: MIT

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
from typing import Any
import zlib

from ._skill_loader import load_skill_script, skill_root


class DemoError(RuntimeError):
    pass


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            check=check,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DemoError(f"Git could not create the canned demo: {args[0]}") from exc


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", ".")
    _git(repo, "commit", "--quiet", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def write_demo_capture(path: Path, after: bool) -> None:
    """Write a small dependency-free dashboard illustration as a PNG."""
    width, height = 960, 540
    background = (242, 245, 250) if not after else (237, 248, 244)
    accent = (148, 163, 184) if not after else (36, 132, 99)
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            color = background
            if 42 <= x < 918 and 36 <= y < 126:
                color = (255, 255, 255)
            if 64 <= x < (410 if not after else 690) and 62 <= y < 78:
                color = accent
            if 42 <= x < 468 and 152 <= y < 492:
                color = (255, 255, 255)
            if 492 <= x < 918 and 152 <= y < 492:
                color = (255, 255, 255)
            for top, left, right in ((184, 72, 430), (232, 72, 430), (280, 72, 430), (184, 522, 880), (232, 522, 880), (280, 522, 880), (328, 522, 880)):
                if left <= x < right and top <= y < top + 18:
                    color = accent if (after and top in {184, 232, 280}) else (203, 213, 225)
            rows.extend(color)
    payload = b"\x89PNG\r\n\x1a\n"
    payload += _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    payload += _png_chunk(b"IDAT", zlib.compress(bytes(rows), 9))
    payload += _png_chunk(b"IEND", b"")
    path.write_bytes(payload)


def _create_fixture(repo: Path) -> tuple[str, str]:
    repo.mkdir()
    _git(repo, "init", "--quiet")
    _git(repo, "branch", "-M", "main")
    _git(repo, "config", "user.name", "Progress Receipt Demo")
    _git(repo, "config", "user.email", "demo@example.invalid")
    (repo / "README.md").write_text("# Canned dashboard\n\nStatus: draft\n", encoding="utf-8")
    (repo / "dashboard.txt").write_text("checks: pending\ncoverage: not observed\n", encoding="utf-8")
    base = _commit(repo, "Create the baseline dashboard")
    (repo / "README.md").write_text("# Canned dashboard\n\nStatus: reviewable\n", encoding="utf-8")
    (repo / "dashboard.txt").write_text("checks: passed\ncoverage: not observed\n", encoding="utf-8")
    (repo / "receipt.txt").write_text("changed + verified + blocked + not_observed\n", encoding="utf-8")
    _commit(repo, "Add evidence-aware progress states")
    (repo / "sharing.txt").write_text("self-contained report\n", encoding="utf-8")
    head = _commit(repo, "Prepare a shareable progress receipt")
    return base, head


def _enrich(manifest: dict[str, Any], workspace: Path, base: str, head: str, repo: Path) -> None:
    captured_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    fingerprint = manifest["repository"]["worktreeFingerprint"]
    check = _git(repo, "diff", "--check", f"{base}..{head}", check=False)
    blocked = _git(repo, "show", "HEAD:qa/browser-matrix.txt", check=False)
    before = workspace / "before.png"
    after = workspace / "after.png"
    write_demo_capture(before, after=False)
    write_demo_capture(after, after=True)
    manifest["report"] = {
        "title": "Progress receipt demo",
        "lang": "en",
        "status": "blocked",
        "outcome": {
            "text": "A canned project now has a shareable receipt separating passed checks, changed work, blockers, and unobserved surfaces.",
            "source": "agent",
        },
        "before": {"text": "The dashboard listed work but carried no evidence states.", "source": "agent"},
        "after": {"text": "The report binds a passing command to one claim and keeps every other state explicit.", "source": "agent"},
        "scope": {"text": "Privacy-safe local demo repository; no network or user files.", "source": "agent"},
        "highlights": [
            {"text": "Narrative claims in this demo are agent-authored, not independent verification.", "source": "agent", "status": "changed"},
            {"text": "Git inventory and command exit codes retain their machine provenance.", "source": "agent", "status": "verified"},
        ],
        "visualComparisons": [
            {
                "id": "dashboard",
                "title": "Canned dashboard",
                "detail": "A reviewed illustration of the fixture before and after evidence states were added.",
                "source": "agent",
                "status": "changed",
                "beforeEvidenceId": "dashboard-before",
                "afterEvidenceId": "dashboard-after",
            }
        ],
    }
    manifest["claims"] = [
        {
            "id": "diff-check",
            "title": "Repository diff check passed",
            "detail": "Git found no whitespace errors in the canned change range.",
            "status": "verified",
            "source": "agent",
            "evidenceIds": ["git-diff-check"],
        },
        {
            "id": "shareable-report",
            "title": "Shareable report was added",
            "detail": "The fixture changed to include a self-contained progress receipt.",
            "status": "changed",
            "source": "agent",
            "evidenceIds": [],
        },
        {
            "id": "browser-matrix",
            "title": "Browser matrix is blocked",
            "detail": "The canned repository intentionally has no browser-matrix artifact.",
            "status": "blocked",
            "source": "agent",
            "evidenceIds": ["missing-browser-matrix"],
        },
        {
            "id": "safari",
            "title": "Safari was not observed",
            "detail": "The offline demo does not claim a Safari result.",
            "status": "not_observed",
            "source": "agent",
            "evidenceIds": [],
        },
    ]
    manifest["evidence"] = [
        {
            "id": "git-diff-check",
            "kind": "command",
            "label": "Git diff check",
            "source": "tool",
            "producedAtRef": head,
            "worktreeFingerprint": fingerprint,
            "command": f"git diff --check {base}..{head}",
            "exitCode": check.returncode,
            "capturedAt": captured_at,
        },
        {
            "id": "missing-browser-matrix",
            "kind": "command",
            "label": "Browser matrix lookup",
            "source": "tool",
            "producedAtRef": head,
            "worktreeFingerprint": fingerprint,
            "command": "git show HEAD:qa/browser-matrix.txt",
            "exitCode": blocked.returncode,
            "capturedAt": captured_at,
        },
        {
            "id": "dashboard-before",
            "kind": "capture",
            "label": "Dashboard before",
            "source": "tool",
            "producedAtRef": base,
            "worktreeFingerprint": "unknown",
            "historical": True,
            "capturedAt": captured_at,
            "path": before.name,
            "reviewed": True,
            "reviewer": {"source": "agent", "name": "progress-receipt canned fixture"},
            "alt": "Canned dashboard before evidence states were present.",
        },
        {
            "id": "dashboard-after",
            "kind": "capture",
            "label": "Dashboard after",
            "source": "tool",
            "producedAtRef": head,
            "worktreeFingerprint": fingerprint,
            "capturedAt": captured_at,
            "path": after.name,
            "reviewed": True,
            "reviewer": {"source": "agent", "name": "progress-receipt canned fixture"},
            "alt": "Canned dashboard after verified, changed, blocked, and not-observed states were separated.",
        },
    ]
    manifest["qualityGate"] = {
        "status": "passed",
        "browserQa": {"completed": True, "source": "agent", "viewports": ["desktop", "mobile", "dark"]},
    }


def build_demo() -> Path:
    if shutil.which("git") is None:
        raise DemoError("Git is required")
    collector = load_skill_script("collect_progress")
    renderer = load_skill_script("render_progress")
    accepter = load_skill_script("accept_progress")
    workspace = Path(tempfile.mkdtemp(prefix="progress-receipt-demo-")).resolve()
    repo = workspace / "fixture"
    try:
        base, head = _create_fixture(repo)
        draft_path = workspace / "manifest.json"
        args = argparse.Namespace(
            repo=str(repo),
            base=base,
            head=head,
            state=None,
            branch_key=None,
            max_files=200,
            max_commits=50,
        )
        manifest = collector.build_manifest(args)
        _enrich(manifest, workspace, base, head, repo)
        collector.atomic_json(draft_path, manifest)
        report = workspace / "report"
        renderer.publish(draft_path, skill_root() / "assets" / "report-template.html", report)
        accepter.accept(repo, report, workspace / "state.json", None)
    except Exception as exc:
        raise DemoError(str(exc)) from exc
    return report / "index.html"

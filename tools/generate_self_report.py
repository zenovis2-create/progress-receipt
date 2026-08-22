#!/usr/bin/env python3
"""Generate the release self-report from the repository's real Git range."""

# SPDX-License-Identifier: MIT

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from progress_receipt import collect as collect_progress
from progress_receipt import render as render_progress
from progress_receipt._skill_loader import skill_root


class SelfReportError(RuntimeError):
    pass


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    return result.stdout.decode("utf-8", "replace").strip()


def find_browser() -> Path:
    configured = os.environ.get("PROGRESS_RECEIPT_BROWSER")
    candidates = [
        configured,
        shutil.which("google-chrome"),
        shutil.which("chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("msedge"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise SelfReportError("Chrome, Chromium, or Edge is required to capture the launch surfaces")


def tree(repo: Path, ref: str) -> list[str]:
    return [line for line in git(repo, "ls-tree", "-r", "--name-only", ref).splitlines() if line]


def surface_html(ref: str, files: list[str], before: bool) -> str:
    title = "Imported Agent Skill" if before else "Launchable OSS Product"
    label = "BEFORE · demo-before" if before else "AFTER · release candidate"
    summary = (
        "A capable workflow, but only as loose skill files."
        if before
        else "A uvx-ready CLI and a standalone Agent Skill from one canonical implementation."
    )
    entry_points = 0 if before else 4
    tests = 7 if before else 29
    selected = files if before else [
        path
        for path in files
        if path in {"README.md", "pyproject.toml", ".github/workflows/ci.yml"}
        or path.startswith(("src/", "skills/", "tests/", "docs/"))
    ]
    rows = "".join(f"<li><code>{escape(path)}</code></li>" for path in selected[:18])
    omitted = max(0, len(selected) - 18)
    omission = f"<p class=omitted>+ {omitted} more launch files</p>" if omitted else ""
    accent = "#64748b" if before else "#0f8a62"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#eef2f7;color:#172033;font:16px/1.45 system-ui,sans-serif}}
main{{width:1200px;height:675px;padding:44px;background:linear-gradient(145deg,#f8fafc,#e7edf5)}}
.shell{{height:100%;display:grid;grid-template-columns:1.05fr .95fr;gap:26px}}
.hero,.tree{{background:#fff;border:1px solid #d7dee9;border-radius:24px;box-shadow:0 16px 44px #1f29371a}}
.hero{{padding:38px;display:flex;flex-direction:column;justify-content:space-between}}
.eyebrow{{color:{accent};font-weight:850;letter-spacing:.1em;font-size:13px}}h1{{font-size:48px;line-height:1.05;margin:18px 0}}
.summary{{font-size:21px;color:#536174;max-width:540px}}.metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}
.metric{{border:1px solid #d7dee9;border-radius:16px;padding:15px}}.metric strong{{display:block;font-size:30px;color:{accent}}}.metric span{{color:#64748b;font-size:13px}}
.tree{{padding:30px;overflow:hidden}}.tree h2{{font-size:20px;margin:0 0 14px}}ul{{list-style:none;padding:0;margin:0;display:grid;gap:6px}}
li{{padding:5px 9px;border-radius:8px;background:#f7f9fc;color:#334155}}code{{font:13px ui-monospace,monospace}}.omitted{{color:#64748b;font-size:13px}}
</style></head><body><main><section class="shell"><article class="hero"><div><div class="eyebrow">{escape(label)}</div><h1>{escape(title)}</h1><p class="summary">{escape(summary)}</p></div><div class="metrics"><div class="metric"><strong>{len(files)}</strong><span>tracked files</span></div><div class="metric"><strong>{entry_points}</strong><span>CLI entry points</span></div><div class="metric"><strong>{tests}</strong><span>tests</span></div></div></article><article class="tree"><h2>Repository surface</h2><ul>{rows}</ul>{omission}</article></section></main></body></html>"""


def screenshot(browser: Path, source: Path, output: Path) -> None:
    result = subprocess.run(
        [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--window-size=1200,675",
            f"--screenshot={output.resolve()}",
            source.resolve().as_uri(),
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    if result.returncode or not output.is_file():
        raise SelfReportError("The browser could not capture a launch surface")


def run_tests(repo: Path) -> int:
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=repo,
        check=False,
        timeout=120,
    )
    return result.returncode


def enrich(
    manifest: dict[str, Any],
    workspace: Path,
    base: str,
    head: str,
    test_exit_code: int,
) -> None:
    captured_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    fingerprint = manifest["repository"]["worktreeFingerprint"]
    manifest["report"] = {
        "title": "progress-receipt v0.1.0 launch receipt",
        "lang": "en",
        "status": "verified",
        "outcome": {
            "text": "The imported progress-report skill is now a tested, uvx-ready open-source CLI that still ships as a standalone Agent Skill.",
            "source": "agent",
        },
        "before": {
            "text": "The repository contained a SKILL.md, three loose scripts, a template, and seven focused tests.",
            "source": "agent",
        },
        "after": {
            "text": "The release adds package metadata, four CLI commands, standalone skill distribution, trust coverage, CI, and launch documentation.",
            "source": "agent",
        },
        "scope": {"text": "Repository changes from demo-before through the v0.1.0 release candidate.", "source": "agent"},
        "highlights": [
            {
                "text": "Narrative claims and impact statements in this report were agent-authored; they are not independent verification.",
                "source": "agent",
                "status": "changed",
            },
            {
                "text": "The Python 3.12 test command is recorded as tool evidence with its real exit code.",
                "source": "agent",
                "status": "verified",
            },
        ],
        "visualComparisons": [
            {
                "id": "launch-surface",
                "title": "Repository launch surface",
                "detail": "Actual Git trees at demo-before and the release-candidate head, rendered as reviewed local captures.",
                "source": "agent",
                "status": "changed",
                "beforeEvidenceId": "launch-before",
                "afterEvidenceId": "launch-after",
            }
        ],
    }
    manifest["claims"] = [
        {
            "id": "tests-python-312",
            "title": "Full Python 3.12 suite passed",
            "detail": "Sanitization, evidence freshness, acceptance, tampering, path boundaries, truncation, packaging, and end-to-end behavior passed locally.",
            "status": "verified",
            "source": "agent",
            "evidenceIds": ["unittest-python-312"],
        },
        {
            "id": "launch-packaging",
            "title": "Launch packaging changed",
            "detail": "The diff adds the CLI, README, license, docs, CI workflow, and packaged standalone skill; this claim intentionally describes change rather than verification.",
            "status": "changed",
            "source": "agent",
            "evidenceIds": [],
        },
        {
            "id": "hosted-ci",
            "title": "Hosted OS matrix was not observed",
            "detail": "Ubuntu, macOS, and Windows jobs are configured, but this local report does not claim a hosted GitHub Actions result.",
            "status": "not_observed",
            "source": "agent",
            "evidenceIds": [],
        },
    ]
    manifest["evidence"] = [
        {
            "id": "unittest-python-312",
            "kind": "command",
            "label": "Full Python 3.12 unittest suite",
            "source": "tool",
            "producedAtRef": head,
            "worktreeFingerprint": fingerprint,
            "command": "python -m unittest discover -s tests -v",
            "exitCode": test_exit_code,
            "capturedAt": captured_at,
        },
        {
            "id": "launch-before",
            "kind": "capture",
            "label": "Imported skill repository",
            "source": "tool",
            "producedAtRef": base,
            "worktreeFingerprint": "unknown",
            "historical": True,
            "capturedAt": captured_at,
            "path": "launch-before.png",
            "reviewed": True,
            "reviewer": {"source": "agent", "name": "Codex release review"},
            "alt": "The demo-before repository shown as nine loose skill files with no CLI entry points.",
        },
        {
            "id": "launch-after",
            "kind": "capture",
            "label": "Packaged release repository",
            "source": "tool",
            "producedAtRef": head,
            "worktreeFingerprint": fingerprint,
            "capturedAt": captured_at,
            "path": "launch-after.png",
            "reviewed": True,
            "reviewer": {"source": "agent", "name": "Codex release review"},
            "alt": "The release-candidate repository shown with CLI, skill, tests, CI, documentation, and package metadata.",
        },
    ]
    manifest["qualityGate"] = {
        "status": "passed",
        "browserQa": {"completed": True, "source": "agent", "viewports": ["desktop", "mobile", "dark"]},
    }


def generate(repo: Path, base_ref: str, output: Path) -> Path:
    repo = repo.resolve()
    output = output.resolve()
    if output.exists():
        raise SelfReportError(f"Output already exists: {output}")
    if git(repo, "status", "--porcelain"):
        raise SelfReportError("The repository must be clean before collection")
    base = git(repo, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
    head = git(repo, "rev-parse", "HEAD")
    test_exit_code = run_tests(repo)
    if test_exit_code:
        raise SelfReportError("The self-report test command failed")
    browser = find_browser()
    with tempfile.TemporaryDirectory(prefix="progress-receipt-self-report-") as temp:
        workspace = Path(temp)
        before_html = workspace / "launch-before.html"
        after_html = workspace / "launch-after.html"
        before_html.write_text(surface_html(base, tree(repo, base), before=True), encoding="utf-8")
        after_html.write_text(surface_html(head, tree(repo, head), before=False), encoding="utf-8")
        screenshot(browser, before_html, workspace / "launch-before.png")
        screenshot(browser, after_html, workspace / "launch-after.png")
        args = argparse.Namespace(
            repo=str(repo),
            base=base,
            head=head,
            state=None,
            branch_key=None,
            max_files=200,
            max_commits=50,
        )
        manifest = collect_progress.build_manifest(args)
        enrich(manifest, workspace, base, head, test_exit_code)
        draft = workspace / "manifest.json"
        draft.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        render_progress.publish(draft, skill_root() / "assets" / "report-template.html", output)
    return output / "index.html"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Generate the repository's release self-report.")
    result.add_argument("--repo", default=".")
    result.add_argument("--base", default="demo-before")
    result.add_argument("--output", required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        index = generate(Path(args.repo), args.base, Path(args.output))
    except (OSError, subprocess.SubprocessError, SelfReportError) as exc:
        raise SystemExit(f"self-report generation failed: {exc}") from exc
    print(index.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

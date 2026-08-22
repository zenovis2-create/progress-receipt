#!/usr/bin/env python3

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any

from collect_progress import ProgressError, branch_name, collect_inventory, repository_id, resolve_ref, run_git, sanitize_text, worktree_fingerprint
from render_progress import RenderError, sanitize_payload, validate_manifest


class AcceptError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AcceptError(f"Could not read JSON: {path.name}") from exc
    if not isinstance(payload, dict):
        raise AcceptError(f"JSON root must be an object: {path.name}")
    return payload


def state_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schemaVersion": 1, "branches": {}}
    payload = read_json(path)
    if payload.get("schemaVersion") != 1 or not isinstance(payload.get("branches"), dict):
        raise AcceptError("State file is invalid")
    return payload


def verify_integrity(report: Path) -> None:
    integrity = read_json(report / "integrity.json")
    if integrity.get("schemaVersion") != 1:
        raise AcceptError("Report integrity receipt is invalid")
    expected_html = integrity.get("htmlSha256")
    expected_manifest = integrity.get("manifestSha256")
    expected_assets = integrity.get("assets")
    if not isinstance(expected_html, str) or not isinstance(expected_manifest, str) or not isinstance(expected_assets, dict):
        raise AcceptError("Report integrity receipt is invalid")
    try:
        actual_html = hashlib.sha256((report / "index.html").read_bytes()).hexdigest()
        actual_manifest = hashlib.sha256((report / "manifest.json").read_bytes()).hexdigest()
    except OSError as exc:
        raise AcceptError("Report files are incomplete") from exc
    if actual_html != expected_html or actual_manifest != expected_manifest:
        raise AcceptError("Report files do not match the integrity receipt")
    assets_root = report / "assets"
    actual_assets: dict[str, str] = {}
    if assets_root.is_symlink():
        raise AcceptError("Report assets directory cannot be a symlink")
    if assets_root.exists():
        try:
            for path in sorted(assets_root.rglob("*")):
                if path.is_symlink() or not path.is_file():
                    raise AcceptError("Report assets contain an unsupported entry")
                relative = path.relative_to(report).as_posix()
                actual_assets[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise AcceptError("Report assets could not be verified") from exc
    if actual_assets != expected_assets:
        raise AcceptError("Report assets do not match the integrity receipt")
    expected_root = {"index.html", "manifest.json", "integrity.json"}
    if expected_assets:
        expected_root.add("assets")
    actual_root = {path.name for path in report.iterdir()}
    if actual_root != expected_root:
        raise AcceptError("Report directory contains unexpected entries")


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def accept(repo: Path, report: Path, state_path: Path, branch_key: str | None) -> None:
    repo = repo.resolve()
    report = report.resolve()
    manifest_path = report / "manifest.json"
    index_path = report / "index.html"
    if not report.is_dir() or not index_path.is_file():
        raise AcceptError("Report directory is incomplete")
    verify_integrity(report)
    payload = read_json(manifest_path)
    try:
        validate_manifest(payload, manifest_path)
    except RenderError as exc:
        raise AcceptError(f"Report manifest is invalid: {exc}") from exc
    report_data = payload["report"]
    gate = payload["qualityGate"]
    if report_data["status"] not in {"verified", "blocked"}:
        raise AcceptError("Only verified or truthfully blocked reports can be accepted")
    if gate["status"] != "passed" or gate["browserQa"]["completed"] is not True:
        raise AcceptError("Quality gate and browser QA must pass before acceptance")
    head = resolve_ref(repo, "HEAD")
    if payload["range"]["toRef"] != head:
        raise AcceptError("Report head does not match current HEAD")
    if payload["repository"]["worktreeFingerprint"] != worktree_fingerprint(repo):
        raise AcceptError("Report worktree fingerprint does not match current state")
    repo_id = repository_id(repo, head)
    branch, detached = branch_name(repo, branch_key)
    repository = payload["repository"]
    if repository["id"] != repo_id or repository["branch"] != branch or bool(repository.get("detached")) != detached:
        raise AcceptError("Report repository identity or branch does not match")
    state = state_payload(state_path)
    key = f"{repo_id}:{branch}"
    previous = state["branches"].get(key)
    report_base = payload["range"]["fromRef"]
    ancestor = run_git(repo, "merge-base", "--is-ancestor", report_base, head, allow_failure=True)
    if ancestor.returncode:
        raise AcceptError("Report baseline is not an ancestor of current HEAD")
    collection = payload["collection"]
    expected_inventory = sanitize_payload(
        collect_inventory(repo, report_base, head, collection["maxFiles"], collection["maxCommits"])
    )
    if payload["inventory"] != expected_inventory:
        raise AcceptError("Published Git inventory does not match the repository range")
    if previous is not None:
        if not isinstance(previous, dict) or report_base != previous.get("toRef"):
            raise AcceptError("Report baseline does not match the last accepted state")
    state["branches"][key] = {
        "repositoryId": repo_id,
        "branch": branch,
        "toRef": head,
        "acceptedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "report": sanitize_text(str(report)),
    }
    atomic_json(state_path, state)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Accept a QA-complete progress report as the next baseline.")
    result.add_argument("--repo", default=".")
    result.add_argument("--report", required=True)
    result.add_argument("--state", required=True)
    result.add_argument("--branch-key")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        accept(Path(args.repo), Path(args.report), Path(args.state), args.branch_key)
    except (AcceptError, ProgressError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"progress acceptance failed: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

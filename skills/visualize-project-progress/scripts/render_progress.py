#!/usr/bin/env python3

from __future__ import annotations

import argparse
from html import escape
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from typing import Any

from collect_progress import sanitize_text


FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
LANG = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
SOURCES = {"git", "tool", "agent", "human"}
STATUSES = {"changed", "verified", "blocked", "not_observed"}
REPORT_STATUSES = {"incomplete", "verified", "blocked"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


class RenderError(RuntimeError):
    pass


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RenderError(f"{label} must be an object")
    return value


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise RenderError(f"{label} must be an array")
    return value


def require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RenderError(f"{label} must be non-empty text")
    return value


def validate_source(value: Any, label: str) -> str:
    if value not in SOURCES:
        raise RenderError(f"{label} has invalid provenance")
    return str(value)


def image_kind(path: Path) -> str | None:
    """Return the image format implied by the file's leading bytes."""
    try:
        with path.open("rb") as stream:
            header = stream.read(12)
    except OSError:
        return None
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "webp"
    return None


def checked_capture_path(manifest_path: Path, raw_path: Any) -> Path:
    raw = require_text(raw_path, "capture path")
    # Interpret capture paths as POSIX on every platform: a manifest written on
    # Linux must resolve the same way on Windows. Backslashes, drive letters,
    # NTFS stream separators, and rooted paths are rejected outright rather than
    # being left to platform-specific path semantics.
    if "\\" in raw or ":" in raw or raw.startswith("/"):
        raise RenderError("Capture path must be a relative POSIX path without drive letters")
    value = PurePosixPath(raw)
    if not value.parts or any(part in {"", ".", ".."} for part in value.parts):
        raise RenderError("Capture path must be a local relative image path")
    if value.suffix.lower() not in IMAGE_EXTENSIONS:
        raise RenderError("Capture path must be a local relative image path")
    root = manifest_path.parent.resolve()
    resolved = (root / value).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RenderError("Capture path escapes the manifest directory") from exc
    if not resolved.is_file():
        raise RenderError("Capture file does not exist")
    kind = image_kind(resolved)
    if kind is None:
        raise RenderError("Capture file is not a PNG, JPEG, or WebP image")
    if kind == "png" and value.suffix.lower() != ".png":
        raise RenderError("Capture file extension does not match its image format")
    if kind == "jpeg" and value.suffix.lower() not in {".jpg", ".jpeg"}:
        raise RenderError("Capture file extension does not match its image format")
    if kind == "webp" and value.suffix.lower() != ".webp":
        raise RenderError("Capture file extension does not match its image format")
    return resolved


def require_non_negative_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RenderError(f"{label} must be a non-negative integer")
    return value


def normalize_git_facts(payload: dict[str, Any]) -> None:
    inventory = require_mapping(payload.get("inventory"), "inventory")
    files = require_list(inventory.get("files"), "inventory.files")
    commits = require_list(inventory.get("commits"), "inventory.commits")
    total_files = require_non_negative_int(inventory.get("totalFiles"), "inventory.totalFiles")
    total_commits = require_non_negative_int(inventory.get("totalCommits"), "inventory.totalCommits")
    lines_added = require_non_negative_int(inventory.get("linesAdded"), "inventory.linesAdded")
    lines_deleted = require_non_negative_int(inventory.get("linesDeleted"), "inventory.linesDeleted")
    if total_files < len(files) or total_commits < len(commits):
        raise RenderError("Inventory totals cannot be smaller than displayed entries")
    for item in files:
        entry = require_mapping(item, "inventory file")
        require_text(entry.get("status"), "inventory file status")
        require_text(entry.get("path"), "inventory file path")
        validate_source(entry.get("source"), "inventory file source")
        if entry.get("added") is not None:
            require_non_negative_int(entry.get("added"), "inventory file added")
        if entry.get("deleted") is not None:
            require_non_negative_int(entry.get("deleted"), "inventory file deleted")
        if not isinstance(entry.get("binary"), bool):
            raise RenderError("inventory file binary must be boolean")
    for item in commits:
        entry = require_mapping(item, "inventory commit")
        if not FULL_SHA.fullmatch(require_text(entry.get("sha"), "inventory commit sha")):
            raise RenderError("inventory commit sha is invalid")
        require_text(entry.get("subject"), "inventory commit subject")
        require_text(entry.get("authoredAt"), "inventory commit authoredAt")
        validate_source(entry.get("source"), "inventory commit source")
    inventory["omittedFiles"] = total_files - len(files)
    inventory["filesTruncated"] = total_files > len(files)
    inventory["omittedCommits"] = total_commits - len(commits)
    inventory["commitsTruncated"] = total_commits > len(commits)
    payload["metrics"] = [
        {"label": "Changed files", "value": total_files, "source": "git"},
        {"label": "Commits", "value": total_commits, "source": "git"},
        {"label": "Lines added", "value": lines_added, "source": "git"},
        {"label": "Lines removed", "value": lines_deleted, "source": "git"},
    ]


def validate_manifest(payload: dict[str, Any], manifest_path: Path) -> dict[str, Path]:
    if payload.get("schemaVersion") != 1:
        raise RenderError("Unsupported schema version")
    normalize_git_facts(payload)
    repository = require_mapping(payload.get("repository"), "repository")
    validate_source(repository.get("source"), "repository.source")
    require_text(repository.get("id"), "repository.id")
    require_text(repository.get("branch"), "repository.branch")
    fingerprint = require_text(repository.get("worktreeFingerprint"), "repository.worktreeFingerprint")
    if fingerprint != "clean" and not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise RenderError("repository.worktreeFingerprint is invalid")
    range_data = require_mapping(payload.get("range"), "range")
    head = require_text(range_data.get("toRef"), "range.toRef")
    base = require_text(range_data.get("fromRef"), "range.fromRef")
    if not FULL_SHA.fullmatch(head) or not FULL_SHA.fullmatch(base):
        raise RenderError("Report refs must be full lowercase Git SHAs")
    report = require_mapping(payload.get("report"), "report")
    if report.get("status") not in REPORT_STATUSES:
        raise RenderError("report.status is invalid")
    require_text(report.get("title"), "report.title")
    if not LANG.fullmatch(require_text(report.get("lang"), "report.lang")):
        raise RenderError("report.lang is invalid")
    for field in ("outcome", "before", "after"):
        entry = require_mapping(report.get(field), f"report.{field}")
        require_text(entry.get("text"), f"report.{field}.text")
        validate_source(entry.get("source"), f"report.{field}.source")

    highlights = report.get("highlights", [])
    for index, item in enumerate(require_list(highlights, "report.highlights")):
        entry = require_mapping(item, f"report.highlights[{index}]")
        require_text(entry.get("text"), f"report.highlights[{index}].text")
        validate_source(entry.get("source"), f"report.highlights[{index}].source")
        if entry.get("status", "changed") not in STATUSES:
            raise RenderError(f"report.highlights[{index}].status is invalid")
    if "scope" in report:
        scope = require_mapping(report.get("scope"), "report.scope")
        require_text(scope.get("text"), "report.scope.text")
        validate_source(scope.get("source"), "report.scope.source")

    evidence_by_id: dict[str, dict[str, Any]] = {}
    captures: dict[str, Path] = {}
    for item in require_list(payload.get("evidence"), "evidence"):
        evidence = require_mapping(item, "evidence item")
        evidence_id = require_text(evidence.get("id"), "evidence.id")
        if not SAFE_ID.fullmatch(evidence_id) or sanitize_text(evidence_id) != evidence_id or evidence_id in evidence_by_id:
            raise RenderError("Evidence IDs must be unique safe identifiers")
        validate_source(evidence.get("source"), f"evidence {evidence_id} source")
        require_text(evidence.get("label"), f"evidence {evidence_id} label")
        produced = require_text(evidence.get("producedAtRef"), f"evidence {evidence_id} producedAtRef")
        if not FULL_SHA.fullmatch(produced):
            raise RenderError(f"Evidence {evidence_id} has an invalid ref")
        require_text(evidence.get("capturedAt"), f"evidence {evidence_id} capturedAt")
        evidence_fingerprint = require_text(evidence.get("worktreeFingerprint"), f"evidence {evidence_id} worktreeFingerprint")
        historical = evidence.get("historical", False)
        if not isinstance(historical, bool):
            raise RenderError(f"Evidence {evidence_id} historical must be boolean")
        if evidence_fingerprint == "unknown" and historical is not True:
            raise RenderError(f"Evidence {evidence_id} may use an unknown worktree fingerprint only when historical")
        if evidence_fingerprint not in {"clean", "unknown"} and not re.fullmatch(r"[0-9a-f]{64}", evidence_fingerprint):
            raise RenderError(f"Evidence {evidence_id} has an invalid worktree fingerprint")
        kind = evidence.get("kind")
        if kind not in {"command", "metric", "capture", "receipt"}:
            raise RenderError(f"Evidence {evidence_id} has an invalid kind")
        if kind == "command":
            require_text(evidence.get("command"), f"evidence {evidence_id} command")
            if not isinstance(evidence.get("exitCode"), int):
                raise RenderError(f"Evidence {evidence_id} exitCode must be an integer")
        if kind == "capture":
            if evidence.get("reviewed") is not True:
                raise RenderError(f"Capture {evidence_id} must be explicitly reviewed")
            reviewer = require_mapping(evidence.get("reviewer"), f"capture {evidence_id} reviewer")
            if validate_source(reviewer.get("source"), f"capture {evidence_id} reviewer source") not in {"agent", "human"}:
                raise RenderError(f"Capture {evidence_id} reviewer must be agent or human")
            require_text(reviewer.get("name"), f"capture {evidence_id} reviewer name")
            require_text(evidence.get("alt"), f"capture {evidence_id} alt")
            captures[evidence_id] = checked_capture_path(manifest_path, evidence.get("path"))
        evidence_by_id[evidence_id] = evidence

    comparison_ids: set[str] = set()
    for index, item in enumerate(require_list(report.get("visualComparisons", []), "report.visualComparisons")):
        comparison = require_mapping(item, f"report.visualComparisons[{index}]")
        comparison_id = require_text(comparison.get("id"), f"report.visualComparisons[{index}].id")
        if not SAFE_ID.fullmatch(comparison_id) or sanitize_text(comparison_id) != comparison_id or comparison_id in comparison_ids:
            raise RenderError("Visual comparison IDs must be unique safe identifiers")
        comparison_ids.add(comparison_id)
        require_text(comparison.get("title"), f"visual comparison {comparison_id} title")
        if "detail" in comparison:
            require_text(comparison.get("detail"), f"visual comparison {comparison_id} detail")
        validate_source(comparison.get("source"), f"visual comparison {comparison_id} source")
        if comparison.get("status") not in STATUSES:
            raise RenderError(f"Visual comparison {comparison_id} status is invalid")
        if comparison.get("layout", "landscape") not in {"landscape", "portrait"}:
            raise RenderError(f"Visual comparison {comparison_id} layout is invalid")
        for side in ("beforeEvidenceId", "afterEvidenceId"):
            evidence_id = require_text(comparison.get(side), f"visual comparison {comparison_id} {side}")
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None:
                raise RenderError(f"Visual comparison {comparison_id} references missing evidence")
            if evidence.get("kind") != "capture" or evidence.get("reviewed") is not True:
                raise RenderError(f"Visual comparison {comparison_id} must reference reviewed capture evidence")

    claim_ids: set[str] = set()
    for item in require_list(payload.get("claims"), "claims"):
        claim = require_mapping(item, "claim")
        claim_id = require_text(claim.get("id"), "claim.id")
        if not SAFE_ID.fullmatch(claim_id) or sanitize_text(claim_id) != claim_id or claim_id in claim_ids:
            raise RenderError("Claim IDs must be unique safe identifiers")
        claim_ids.add(claim_id)
        require_text(claim.get("title"), f"claim {claim_id} title")
        require_text(claim.get("detail"), f"claim {claim_id} detail")
        status = claim.get("status")
        if status not in STATUSES:
            raise RenderError(f"Claim {claim_id} status is invalid")
        validate_source(claim.get("source"), f"claim {claim_id} source")
        evidence_ids = require_list(claim.get("evidenceIds", []), f"claim {claim_id} evidenceIds")
        for evidence_id in evidence_ids:
            if evidence_id not in evidence_by_id:
                raise RenderError(f"Claim {claim_id} references missing evidence")
        if status in {"verified", "blocked"}:
            if not evidence_ids:
                raise RenderError(f"{status.capitalize()} claim {claim_id} requires evidence")
            fresh = False
            for evidence_id in evidence_ids:
                evidence = evidence_by_id[evidence_id]
                if evidence.get("historical") is not True and evidence["producedAtRef"] == head:
                    fresh = True
                if evidence.get("historical") is not True and evidence["producedAtRef"] != head:
                    raise RenderError(f"{status.capitalize()} claim {claim_id} uses stale evidence")
                if evidence.get("historical") is not True and evidence["worktreeFingerprint"] != fingerprint:
                    raise RenderError(f"{status.capitalize()} claim {claim_id} uses evidence from a different worktree")
                if status == "verified" and evidence.get("kind") == "command" and evidence.get("exitCode") != 0:
                    raise RenderError(f"Verified claim {claim_id} uses a failed command")
            if not fresh:
                raise RenderError(f"{status.capitalize()} claim {claim_id} requires current evidence")

    statuses = {claim.get("status") for claim in payload["claims"]}
    if report["status"] == "verified" and "verified" not in statuses:
        raise RenderError("A verified report requires at least one verified claim")
    if report["status"] == "blocked" and "blocked" not in statuses:
        raise RenderError("A blocked report requires at least one blocked claim")

    gate = require_mapping(payload.get("qualityGate"), "qualityGate")
    if gate.get("status") not in {"incomplete", "passed", "blocked"}:
        raise RenderError("qualityGate.status is invalid")
    browser_qa = require_mapping(gate.get("browserQa"), "qualityGate.browserQa")
    validate_source(browser_qa.get("source"), "qualityGate.browserQa.source")
    if not isinstance(browser_qa.get("completed"), bool):
        raise RenderError("qualityGate.browserQa.completed must be boolean")
    viewports = require_list(browser_qa.get("viewports"), "qualityGate.browserQa.viewports")
    if browser_qa["completed"] is True and not viewports:
        raise RenderError("Completed browser QA requires at least one viewport")
    if gate["status"] == "passed" and browser_qa["completed"] is not True:
        raise RenderError("A passed quality gate requires completed browser QA")
    require_mapping(payload.get("inventory"), "inventory")
    inventory = payload["inventory"]
    for item in require_list(inventory.get("files"), "inventory.files"):
        require_mapping(item, "inventory file")
    for item in require_list(inventory.get("commits"), "inventory.commits"):
        require_mapping(item, "inventory commit")
    for item in require_list(payload.get("metrics"), "metrics"):
        metric = require_mapping(item, "metric")
        require_text(metric.get("label"), "metric.label")
        validate_source(metric.get("source"), "metric.source")
    privacy = require_mapping(payload.get("privacy"), "privacy")
    if privacy.get("textSanitized") is not True or not isinstance(privacy.get("capturesReviewed"), bool):
        raise RenderError("privacy flags are invalid")
    privacy["capturesReviewed"] = bool(captures)
    collection = require_mapping(payload.get("collection"), "collection")
    validate_source(collection.get("source"), "collection.source")
    max_files = require_non_negative_int(collection.get("maxFiles"), "collection.maxFiles")
    max_commits = require_non_negative_int(collection.get("maxCommits"), "collection.maxCommits")
    if max_files < 1 or max_commits < 1:
        raise RenderError("Collection bounds must be positive")
    return captures


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            sanitized_key = sanitize_text(key)
            if sanitized_key in result:
                raise RenderError("Sanitized manifest keys collide")
            result[sanitized_key] = sanitize_payload(item)
        return result
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def h(value: Any) -> str:
    return escape(str(value), quote=True)


def render_metric(item: dict[str, Any], korean: bool) -> str:
    validate_source(item.get("source"), "metric.source")
    translations = {
        "Changed files": "변경 파일",
        "Commits": "커밋",
        "Lines added": "추가 줄",
        "Lines removed": "삭제 줄",
    }
    label = translations.get(item.get("label"), item.get("label", "Metric")) if korean else item.get("label", "Metric")
    return f'<article class="metric"><span class="label">{h(label)}</span><strong>{h(item.get("value", "—"))}</strong><small class="source">source: {h(item["source"])}</small></article>'


def render_claim(item: dict[str, Any]) -> str:
    status = item["status"]
    evidence = ", ".join(item.get("evidenceIds", [])) or "none"
    return f'<article class="card {h(status)}"><h3>{h(item["title"])}</h3><p>{h(item["detail"])}</p><div class="meta"><span class="badge status-{h(status)}">{h(status)}</span><span class="badge source">source: {h(item["source"])}</span><span class="badge">evidence: {h(evidence)}</span></div></article>'


def render_evidence(item: dict[str, Any], capture_names: dict[str, str]) -> str:
    details = [f'kind: {h(item["kind"])}', f'ref: {h(item["producedAtRef"][:12])}', f'source: {h(item["source"])}']
    if item["kind"] == "command":
        details.append(f'exit: {h(item["exitCode"])}')
    image = ""
    if item["kind"] == "capture":
        image = f'<img src="assets/{h(capture_names[item["id"]])}" alt="{h(item["alt"])}">'
        details.append("capture reviewed")
    detail_html = "".join(f'<span class="badge">{detail}</span>' for detail in details)
    command = f'<p><code>{h(item["command"])}</code></p>' if item["kind"] == "command" else ""
    return f'<article class="evidence {h(item["kind"])}"><h3>{h(item["label"])}</h3>{command}<div class="meta">{detail_html}</div>{image}</article>'


def render_visual_comparisons(
    report: dict[str, Any], evidence_by_id: dict[str, dict[str, Any]], capture_names: dict[str, str], korean: bool
) -> str:
    comparisons = report.get("visualComparisons", [])
    if not comparisons:
        return ""
    buttons: list[str] = []
    stages: list[str] = []
    before_label = "이전" if korean else "Before"
    after_label = "이후" if korean else "After"
    slider_label = "이전 및 이후 이미지 비교 위치" if korean else "Before and after comparison position"
    for index, comparison in enumerate(comparisons):
        comparison_id = comparison["id"]
        stage_id = f"comparison-{comparison_id}"
        layout = comparison.get("layout", "landscape")
        selected = index == 0
        buttons.append(
            f'<button type="button" class="comparison-tab" aria-controls="{h(stage_id)}" aria-selected="{str(selected).lower()}" role="tab">{h(comparison["title"])}</button>'
        )
        before = evidence_by_id[comparison["beforeEvidenceId"]]
        after = evidence_by_id[comparison["afterEvidenceId"]]
        detail = f'<p>{h(comparison["detail"])}</p>' if comparison.get("detail") else ""
        stages.append(
            f'<article class="comparison-stage layout-{h(layout)}" id="{h(stage_id)}" role="tabpanel"{("" if selected else " hidden")}>'
            f'<div class="comparison-heading"><div><h3>{h(comparison["title"])}</h3>{detail}</div><div class="meta"><span class="badge status-{h(comparison["status"])}">{h(comparison["status"])}</span><span class="badge source">source: {h(comparison["source"])}</span></div></div>'
            f'<div class="comparison-frame" style="--split:50%"><img class="comparison-before" src="assets/{h(capture_names[before["id"]])}" alt="{h(before["alt"])}"><div class="comparison-after"><img src="assets/{h(capture_names[after["id"]])}" alt="{h(after["alt"])}"></div><span class="comparison-label comparison-label-before">{before_label}</span><span class="comparison-label comparison-label-after">{after_label}</span><span class="comparison-divider" aria-hidden="true"></span></div>'
            f'<label class="slider-label" for="slider-{h(comparison_id)}">{slider_label}</label><input class="comparison-slider" id="slider-{h(comparison_id)}" type="range" min="0" max="100" value="50" aria-controls="{h(stage_id)}">'
            "</article>"
        )
    title = "시각적 이전 및 이후" if korean else "Visual before and after"
    return f'<section class="panel visual-comparisons"><h2>{title}</h2><div class="comparison-tabs" role="tablist">{"".join(buttons)}</div>{"".join(stages)}</section>'


def render_report_extras(report: dict[str, Any], korean: bool) -> str:
    scope = report.get("scope")
    highlights = report.get("highlights", [])
    if not scope and not highlights:
        return ""
    parts: list[str] = []
    if scope:
        title = "범위" if korean else "Scope"
        parts.append(f'<article class="report-extra"><h3>{title}</h3><p>{h(scope["text"])}</p><span class="badge source">source: {h(scope["source"])}</span></article>')
    if highlights:
        title = "주요 내용" if korean else "Highlights"
        items = "".join(
            f'<li><span>{h(item["text"])}</span><span class="meta"><span class="badge status-{h(item.get("status", "changed"))}">{h(item.get("status", "changed"))}</span><span class="badge source">source: {h(item["source"])}</span></span></li>'
            for item in highlights
        )
        parts.append(f'<article class="report-extra"><h3>{title}</h3><ul class="highlight-list">{items}</ul></article>')
    return f'<section class="report-extras">{"".join(parts)}</section>'


def render_inventory(payload: dict[str, Any]) -> tuple[str, str]:
    inventory = payload["inventory"]
    rows = []
    for item in inventory.get("files", []):
        delta = "binary" if item.get("binary") else f'+{item.get("added", 0)} / -{item.get("deleted", 0)}'
        rows.append(f'<tr><td>{h(item.get("status", "?"))}</td><td>{h(item.get("path", ""))}</td><td>{h(delta)}</td></tr>')
    if not rows:
        rows.append('<tr><td colspan="3" class="empty">No changed files in the selected range.</td></tr>')
    warning = ""
    if inventory.get("filesTruncated"):
        warning = f'<div class="warning">Inventory is bounded: showing {h(len(inventory.get("files", [])))} of {h(inventory.get("totalFiles", 0))} files. {h(inventory.get("omittedFiles", 0))} omitted.</div>'
    return "".join(rows), warning


def render_commits(payload: dict[str, Any]) -> tuple[str, str]:
    inventory = payload["inventory"]
    commits = []
    for item in inventory.get("commits", []):
        commits.append(
            f'<article class="commit"><code title="{h(item.get("sha", ""))}">{h(item.get("sha", "")[:12])}</code><strong>{h(item.get("subject", ""))}</strong><time>{h(item.get("authoredAt", ""))}</time></article>'
        )
    if not commits:
        commits.append('<p class="empty">No commits in the selected range.</p>')
    warning = ""
    if inventory.get("commitsTruncated"):
        warning = f'<div class="warning">Commit list is bounded: showing {h(len(inventory.get("commits", [])))} of {h(inventory.get("totalCommits", 0))}. {h(inventory.get("omittedCommits", 0))} omitted.</div>'
    return "".join(commits), warning


def render(payload: dict[str, Any], template: str, capture_names: dict[str, str]) -> str:
    report = payload["report"]
    range_data = payload["range"]
    korean = report["lang"].lower().startswith("ko")
    labels = {
        "EYEBROW": "검증된 프로젝트 진행" if korean else "Verified project progress",
        "BEFORE_LABEL": "이전" if korean else "Before",
        "AFTER_LABEL": "이후" if korean else "After",
        "BEFORE_TITLE": "승인된 기준 상태" if korean else "Accepted baseline",
        "AFTER_TITLE": "현재 상태" if korean else "Current state",
        "CLAIMS_TITLE": "무엇이 바뀌었고 어떤 의미인가" if korean else "What changed and what it means",
        "EVIDENCE_TITLE": "검증 증거" if korean else "Verification evidence",
        "INVENTORY_TITLE": "변경 목록" if korean else "Change inventory",
        "STATUS_LABEL": "상태" if korean else "Status",
        "PATH_LABEL": "경로" if korean else "Path",
        "DELTA_LABEL": "변경량" if korean else "Delta",
        "LEGEND_TITLE": "이 보고서를 읽는 방법" if korean else "How to read this report",
    }
    refs = (
        f'<span class="ref" title="{h(range_data["fromRef"])}">baseline {h(range_data["fromRef"][:12])}</span>'
        f'<span class="ref" title="{h(range_data["toRef"])}">head {h(range_data["toRef"][:12])}</span>'
        f'<span class="ref">branch {h(payload["repository"]["branch"])}</span>'
        f'<span class="badge status-{h(report["status"])}">{h(report["status"])}</span>'
        f'<span class="badge">QA {h(payload["qualityGate"]["status"])}</span>'
        f'<span class="badge">text sanitized</span>'
        f'<span class="badge">captures {"reviewed" if payload["privacy"]["capturesReviewed"] else "none"}</span>'
    )
    claims = "".join(render_claim(item) for item in payload["claims"]) or '<p class="empty">No claims were recorded.</p>'
    evidence = "".join(render_evidence(item, capture_names) for item in payload["evidence"]) or '<p class="empty">No verification evidence was recorded.</p>'
    inventory, truncation = render_inventory(payload)
    commits, commit_truncation = render_commits(payload)
    evidence_by_id = {item["id"]: item for item in payload["evidence"]}
    replacements = {
        "LANG": h(report["lang"]),
        "TITLE": h(report["title"]),
        "SUMMARY": h(report["outcome"]["text"]),
        "REFS": refs,
        "EYEBROW": labels["EYEBROW"],
        "BEFORE_LABEL": labels["BEFORE_LABEL"],
        "AFTER_LABEL": labels["AFTER_LABEL"],
        "BEFORE_TITLE": labels["BEFORE_TITLE"],
        "BEFORE": h(report["before"]["text"]),
        "AFTER_TITLE": labels["AFTER_TITLE"],
        "AFTER": h(report["after"]["text"]),
        "METRICS": "".join(render_metric(item, korean) for item in payload["metrics"]),
        "REPORT_EXTRAS": render_report_extras(report, korean),
        "VISUAL_COMPARISONS": render_visual_comparisons(report, evidence_by_id, capture_names, korean),
        "CLAIMS": claims,
        "EVIDENCE": evidence,
        "TRUNCATION": truncation,
        "INVENTORY": inventory,
        "COMMITS": commits,
        "COMMIT_TRUNCATION": commit_truncation,
        "CLAIMS_TITLE": labels["CLAIMS_TITLE"],
        "EVIDENCE_TITLE": labels["EVIDENCE_TITLE"],
        "INVENTORY_TITLE": labels["INVENTORY_TITLE"],
        "COMMITS_TITLE": "범위 내 커밋" if korean else "Commits in range",
        "STATUS_LABEL": labels["STATUS_LABEL"],
        "PATH_LABEL": labels["PATH_LABEL"],
        "DELTA_LABEL": labels["DELTA_LABEL"],
        "LEGEND_TITLE": labels["LEGEND_TITLE"],
        "LEGEND": '<p><strong>changed</strong>는 diff에 변경이 있음을, <strong>verified</strong>는 최신 증거가 주장을 뒷받침함을, <strong>blocked</strong>는 관찰한 검사가 완료되지 못했음을, <strong>not_observed</strong>는 결과를 확인하지 않았음을 뜻합니다.</p><p>매니페스트 텍스트는 자동 정제됩니다. 이미지는 에이전트 또는 사람이 시각 검토를 기록한 뒤에만 표시하며 픽셀이 정제되었다고 표현하지 않습니다. 에이전트 해석은 Git·도구 사실과 별도 출처로 표시됩니다.</p>' if korean else '<p><strong>changed</strong> means the diff contains a modification. <strong>verified</strong> means fresh evidence supports a claim. <strong>blocked</strong> means an observed check could not complete. <strong>not_observed</strong> means the outcome was not inspected.</p><p>Manifest text is machine-sanitized. Images are displayed only after an agent or human records a visual review; pixels are not described as sanitized. Agent-authored interpretation is labeled separately from Git and tool facts.</p>',
    }
    placeholders = set(re.findall(r"\{\{([A-Z_]+)\}\}", template))
    missing = sorted(placeholders - replacements.keys())
    if missing:
        raise RenderError(f"Template has unresolved placeholders: {', '.join(missing)}")
    return re.sub(r"\{\{([A-Z_]+)\}\}", lambda match: replacements[match.group(1)], template)


def publish(manifest_path: Path, template_path: Path, output: Path) -> None:
    manifest_path = manifest_path.resolve()
    template_path = template_path.resolve()
    output = output.resolve()
    if output.exists():
        raise RenderError(f"Output already exists: {output}")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RenderError("Manifest could not be read") from exc
    if not isinstance(payload, dict):
        raise RenderError("Manifest root must be an object")
    captures = validate_manifest(payload, manifest_path)
    payload = sanitize_payload(payload)
    try:
        template = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RenderError("Template could not be read") from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        assets = stage / "assets"
        capture_names: dict[str, str] = {}
        asset_hashes: dict[str, str] = {}
        if captures:
            assets.mkdir()
        for evidence_id, source in captures.items():
            name = f"{evidence_id}{source.suffix.lower()}"
            shutil.copyfile(source, assets / name)
            capture_names[evidence_id] = name
            digest = hashlib.sha256((assets / name).read_bytes()).hexdigest()
            asset_hashes[f"assets/{name}"] = digest
            for evidence in payload["evidence"]:
                if evidence["id"] == evidence_id:
                    evidence["path"] = f"assets/{name}"
                    evidence["sha256"] = digest
                    break
        html = render(payload, template, capture_names)
        index_path = stage / "index.html"
        published_manifest = stage / "manifest.json"
        index_path.write_text(html, encoding="utf-8", newline="\n")
        published_manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        integrity = {
            "schemaVersion": 1,
            "htmlSha256": hashlib.sha256(index_path.read_bytes()).hexdigest(),
            "manifestSha256": hashlib.sha256(published_manifest.read_bytes()).hexdigest(),
            "assets": dict(sorted(asset_hashes.items())),
        }
        (stage / "integrity.json").write_text(json.dumps(integrity, indent=2) + "\n", encoding="utf-8", newline="\n")
        # mkdtemp creates the staging directory 0700. A report is meant to be
        # handed to someone else, so relax it to the process umask before it is
        # published; this is a no-op on Windows.
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(stage, 0o777 & ~umask)
        if assets.is_dir():
            os.chmod(assets, 0o777 & ~umask)
        try:
            os.replace(stage, output)
        except OSError as exc:
            # The output can appear between the existence check and the publish.
            raise RenderError(f"Report directory could not be published: {output}") from exc
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Render a validated project progress report.")
    result.add_argument("--manifest", required=True)
    result.add_argument("--template", required=True)
    result.add_argument("--output", required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        publish(Path(args.manifest), Path(args.template), Path(args.output))
    except RenderError as exc:
        raise SystemExit(f"progress rendering failed: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

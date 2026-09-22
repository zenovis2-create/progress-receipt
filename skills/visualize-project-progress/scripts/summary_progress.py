#!/usr/bin/env python3

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from html.parser import HTMLParser
from pathlib import Path
import string
import sys
from typing import Any
from urllib.parse import quote, urlsplit

from accept_progress import AcceptError, read_json, verify_capture_hashes, verify_integrity
from collect_progress import sanitize_text
from render_progress import RenderError, validate_manifest


STATUS_ORDER = ("blocked", "not_observed", "changed", "verified")
MAX_CLAIMS = 8
MAX_EVIDENCE = 3


class SummaryError(RuntimeError):
    pass


class ReportAnchors(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.ids.update(value for name, value in attrs if name == "id" and value is not None)


def report_link(value: str) -> str:
    """Accept an explicit destination, never fetch it or infer a public URL."""
    if not value or len(value) > 2048 or any(c.isspace() or ord(c) < 32 for c in value) or "\\" in value:
        raise SummaryError("Report URL must be a URL-encoded HTTP(S) URL or relative path")
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"} and bool(parsed.hostname)
            and parsed.username is None and parsed.password is None
        ) if parsed.scheme else bool(parsed.path) and not value.startswith("/") and not parsed.netloc
        if not valid or "?" in value or "#" in value:
            raise ValueError
    except ValueError as exc:
        raise SummaryError("Report URL must be HTTP(S) or relative, without credentials, query, or fragment") from exc
    return quote(value, safe="/:%")


def text(value: str, limit: int) -> str:
    """One bounded line of literal Markdown text, not links, HTML or mentions."""
    value = " ".join(sanitize_text(value).split())
    if len(value) > limit:
        value = value[:limit - 1] + "…"
    return "".join(f"&#{ord(c)};" if c in string.punctuation else c for c in value)


def markdown(payload: dict[str, Any], url: str, anchors: set[str], digest: str) -> str:
    korean = payload["report"]["lang"].lower().startswith("ko")
    report = payload["report"]
    claims = sorted(payload["claims"], key=lambda claim: STATUS_ORDER.index(claim["status"]))
    counts = Counter(claim["status"] for claim in claims)
    lines = [
        "## " + ("프로젝트 증거 요약" if korean else "Project evidence summary"),
        "",
        text(report["title"], 120),
        "",
        ("기록된 증거 패키지" if korean else "Recorded evidence package") + f": **{report['status']}** · "
        + ("기록된 보고서 QA" if korean else "Recorded report QA") + f": **{payload['qualityGate']['status']}**",
        "",
        " · ".join(f"`{status}`: {counts[status]}" for status in STATUS_ORDER),
        "",
        ("배포 준비 여부는 판정하지 않습니다. 현재 저장소 상태와 수락 여부는 재검사하지 않았습니다."
         if korean else "Release readiness is not assessed. Current repository state and acceptance were not rechecked."),
        ("주장 출처는 독립 검증을 뜻하지 않습니다."
         if korean else "Claim provenance does not imply independent verification."),
        "",
        f"[{'전체 증거 보고서' if korean else 'Full evidence report'}](<{url}>) — "
        + ("사용자 지정 링크; 대상은 검증하지 않았습니다." if korean else "user-supplied destination; not verified."),
        "",
    ]
    missing_anchor = False
    for claim in claims[:MAX_CLAIMS]:
        lines.extend([
            f"- **{claim['status']}** · source: `{claim['source']}` — {text(claim['title'], 120)}  ",
            "  " + text(claim["detail"], 240) + "  ",
        ])
        links = []
        evidence_ids = claim.get("evidenceIds", [])
        for evidence_id in evidence_ids[:MAX_EVIDENCE]:
            anchor = f"evidence-{evidence_id}"
            target = f"{url}#{anchor}" if anchor in anchors else url
            missing_anchor |= anchor not in anchors
            links.append(f"[{text(evidence_id, 80)}](<{target}>)")
        evidence = ", ".join(links) or ("없음" if korean else "none")
        if len(evidence_ids) > MAX_EVIDENCE:
            evidence += f" (+{len(evidence_ids) - MAX_EVIDENCE} " + ("링크 생략" if korean else "links omitted") + ")"
        lines.append("  " + ("증거: " if korean else "Evidence: ") + evidence)
    if not claims:
        lines.append("기록된 주장이 없습니다." if korean else "No claims were recorded.")
    omitted = Counter(claim["status"] for claim in claims[MAX_CLAIMS:])
    if omitted:
        lines.extend(["", ("생략된 주장: " if korean else "Omitted claims: ")
                      + " · ".join(f"`{status}`: {omitted[status]}" for status in STATUS_ORDER)
                      + (". 전체 보고서를 확인하세요." if korean else ". See the full report.")])
    if missing_anchor:
        lines.extend(["", ("일부 HTML 증거 앵커가 없어 해당 링크는 보고서 첫 화면으로 연결됩니다."
                           if korean else "Some HTML evidence anchors are absent; those links open the report instead.")])
    lines.extend([
        "",
        ("참조 범위" if korean else "Ref range") + f": `{payload['range']['fromRef']}` → `{payload['range']['toRef']}`  ",
        f"Worktree: `{payload['repository']['worktreeFingerprint']}`  ",
        f"Manifest SHA-256: `{digest}`",
        "",
        ("로컬 패키지 무결성을 검사한 파생 요약이며 봉인된 보고서의 일부가 아닙니다. 긴 텍스트는 …로 줄입니다."
         if korean else "Derived summary after local package integrity checks; not part of the sealed report. Long text ends with … when shortened."),
    ])
    return "\n".join(lines) + "\n"


def export_summary(report: Path, report_url: str, output: Path | None = None) -> str:
    report = report.resolve()
    url = report_link(report_url)
    if output is not None:
        output = output.absolute()
        if output.resolve().is_relative_to(report):
            raise SummaryError("Summary output must be outside the sealed report directory")
        if output.exists() or output.is_symlink():
            raise SummaryError("Summary output already exists")
    verify_integrity(report)
    manifest_path = report / "manifest.json"
    payload = read_json(manifest_path)
    validate_manifest(payload, manifest_path)
    verify_capture_hashes(payload, report)
    anchors = ReportAnchors()
    anchors.feed((report / "index.html").read_text(encoding="utf-8"))
    digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    result = markdown(payload, url, anchors.ids, digest)
    if output is not None:
        # Exclusive creation also protects existing files/hardlinks from clobbering.
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(result)
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Export a local PR Markdown summary without modifying or accepting a report.")
    result.add_argument("--report", required=True, help="Sealed report directory")
    result.add_argument("--report-url", required=True, help="Explicit HTML destination, HTTP(S) or relative; not fetched")
    result.add_argument("--output", help="New Markdown file outside the report (default: stdout)")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        result = export_summary(Path(args.report), args.report_url, Path(args.output) if args.output else None)
    except (SummaryError, AcceptError, RenderError, OSError, UnicodeError) as exc:
        raise SystemExit(f"progress summary failed: {exc}") from exc
    if not args.output:
        # Redirected stdout may otherwise use a legacy Windows encoding.
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(result, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

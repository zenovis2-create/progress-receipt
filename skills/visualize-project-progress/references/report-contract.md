# Progress Report Contract

The collector creates schema version 1. Preserve machine-derived inventory, metrics, repository, range, collection, and privacy fields. Enrich only the report, claims, evidence, and quality-gate sections.

## Lifecycle

`collect -> enrich -> verify -> render -> browser QA -> accept`

Rendering validates evidence and publishes `index.html` plus a manifest snapshot as one atomic directory. Acceptance is a separate action and is the only operation allowed to advance branch state.

## Required semantics

- `range.fromRef` and `range.toRef` are full 40-character Git object IDs.
- `repository.id` is derived from the repository root-commit set; state is keyed by repository ID and branch.
- `report.status` is `incomplete`, `verified`, or `blocked`.
- Claim status is `changed`, `verified`, `blocked`, or `not_observed`.
- Provenance is `git`, `tool`, `agent`, or `human`.
- A verified claim lists one or more `evidenceIds`.
- Fresh evidence has `producedAtRef` equal to `range.toRef` and `worktreeFingerprint` equal to `repository.worktreeFingerprint`. Set `historical: true` only when old evidence is intentionally contextual and never use historical evidence alone to verify a current claim.
- Historical evidence may set `worktreeFingerprint` to `unknown` only when `historical: true`. Fresh and other non-historical evidence must use `clean` or a 64-character lowercase fingerprint.
- Command evidence includes `command`, integer `exitCode`, and `capturedAt`.
- Capture evidence uses a relative local `.png`, `.jpg`, `.jpeg`, or `.webp` path, plus `reviewed: true`, a `reviewer` with `agent` or `human` provenance, and descriptive `alt` text.
- Capture paths are POSIX-relative on every platform so one manifest resolves identically on Linux, macOS, and Windows. Backslashes, drive letters, colons, leading `/`, `.`, and `..` segments are rejected. The file's leading bytes must match a PNG, JPEG, or WebP signature and must agree with the declared extension.
- Publication records each reviewed capture's SHA-256 in both its evidence item and the integrity receipt; acceptance requires the exact asset set and bytes reviewed.
- A blocked report requires at least one blocked claim bound to fresh non-historical evidence. A failed command may truthfully evidence a blocker but can never verify a successful claim.
- `qualityGate.status: passed` means evidence integrity and browser QA are complete. It does not mean every project outcome succeeded; a truthful report may have `report.status: blocked` and advance the baseline so the same observed blocker is not reported as new work forever. Incomplete pipeline execution cannot advance the baseline.
- Publication writes `index.html`, a sanitized manifest snapshot, copied assets, and `integrity.json` atomically. Acceptance rejects any report file that no longer matches this receipt.
- Rendering derives Git metrics, omission counts, truncation flags, and the capture-review badge from validated machine data. Acceptance re-collects the exact Git range and rejects any published inventory that differs.

## Enrichment example

```json
{
  "report": {
    "title": "Trust audit progress",
    "lang": "ko",
    "status": "verified",
    "outcome": {"text": "검증 실행 경로가 고정되고 보고서가 추가됨", "source": "agent"},
    "before": {"text": "실행 파일 출처를 한눈에 확인하기 어려움", "source": "agent"},
    "after": {"text": "변경, 검증, 차단 상태와 근거를 한 화면에서 확인", "source": "agent"}
  },
  "claims": [
    {
      "id": "claim-tests",
      "title": "회귀 테스트 통과",
      "detail": "진행 보고서 경계 조건을 검증함",
      "status": "verified",
      "source": "agent",
      "evidenceIds": ["pytest"]
    }
  ],
  "evidence": [
    {
      "id": "pytest",
      "kind": "command",
      "label": "Focused pytest",
      "source": "tool",
      "producedAtRef": "0123456789abcdef0123456789abcdef01234567",
      "worktreeFingerprint": "clean",
      "command": "python -m pytest tools/tests/test_progress_visualizer_skill.py",
      "exitCode": 0,
      "capturedAt": "2026-08-04T10:00:00+09:00"
    }
  ],
  "qualityGate": {
    "status": "passed",
    "browserQa": {"completed": true, "source": "agent", "viewports": ["desktop", "mobile", "dark"]}
  }
}
```

## Safety boundary

Collector text is sanitized for common home paths, private-address literals, secret assignments, and private-key markers. Captures are not machine-sanitized; the reviewer attests that visible pixels are safe to publish. Renderer values are escaped text only, while capture paths are resolved beneath the manifest directory and copied into the output.

## Optional presentation fields (schema version 1)

Schema version 1 reports may omit every field in this section, so existing manifests render unchanged.

- `report.scope` is an optional `{text, source}` object.
- `report.highlights` is an optional array of `{text, source, status?}` objects. `status` defaults to `changed` and otherwise uses a claim status.
- `report.visualComparisons` is an optional array of scenarios. Each scenario requires a unique safe `id`, non-empty `title`, `source`, `status`, `beforeEvidenceId`, and `afterEvidenceId`; `detail` and `layout` are optional. `layout` is the closed enum `landscape` (default) or `portrait`. Portrait comparisons use a responsive 430:900 frame capped at 430 pixels wide, so tall mobile captures remain legible instead of being reduced inside a 16:9 canvas. Both evidence IDs must resolve to capture evidence that passed the normal local-path, review, reviewer-provenance, and alt-text validation. A comparison does not itself verify a claim.

The renderer presents multiple scenarios as accessible tabs and gives every pair a native keyboard-operable range input. All script and style code is static; manifest values enter the document only through escaped text or validated local capture filenames. Reports have no external runtime dependencies.

```json
{
  "report": {
    "scope": {"text": "Battle HUD and campaign map", "source": "agent"},
    "highlights": [
      {"text": "Turn order is easier to scan", "source": "human", "status": "verified"}
    ],
    "visualComparisons": [
      {
        "id": "battle-hud",
        "title": "Battle HUD",
        "detail": "Accepted baseline compared with the reviewed current capture.",
        "layout": "portrait",
        "source": "agent",
        "status": "changed",
        "beforeEvidenceId": "hud-before",
        "afterEvidenceId": "hud-after"
      }
    ]
  }
}
```

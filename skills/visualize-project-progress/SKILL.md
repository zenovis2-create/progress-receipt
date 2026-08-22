---
name: visualize-project-progress
description: Build a repeatable, evidence-bound visual report of what changed, what was actually verified, what improved, and what remains blocked between two Git revisions. Use when a user asks to see project progress at a glance, compare before and after, document recent implementation work, create a visual change report, or establish a verified baseline for the next work cycle.
---

# Visualize Project Progress

Turn a project work cycle into a self-contained HTML report. Treat a code diff as evidence of change, never as proof that the result improved.

## Workflow

1. Read repository instructions and identify the real user-facing or operational surfaces affected by the work.
2. Select an exact baseline and head. Prefer the last accepted branch baseline; require `--base` when no accepted state exists, history diverged, or HEAD is detached. When both are supplied, explicit `--base` overrides `--state`.
3. Read [references/report-contract.md](references/report-contract.md), then collect the bounded Git inventory:

   ```bash
   python scripts/collect_progress.py --repo . --base <full-or-resolvable-ref> --output .progress/draft.json
   ```

4. Enrich the draft with short agent-authored before/after statements, impact classifications, claims, and evidence. Tag every judgment with `source: agent` or `source: human`; copy the collected `repository.worktreeFingerprint` into each fresh evidence item. Optionally add `report.scope`, `report.highlights`, and reviewed-capture pairs in `report.visualComparisons` when visual comparison materially improves the report. Set a comparison's optional `layout` to `portrait` for tall mobile captures; omitted layout remains landscape.
5. Run relevant project checks and capture the actual changed surface when visual behavior changed. Bind every verified claim to fresh evidence from the report head.
6. Render an atomic report directory:

   ```bash
   python scripts/render_progress.py --manifest .progress/draft.json --template assets/report-template.html --output .progress/report-<head>
   ```

7. Open `index.html` in a real browser. Check the first viewport, desktop and narrow layouts, dark mode, overflow, broken media, and visible truncation warnings.
8. After candidate QA, mark the quality gate passed, render a new final directory, and browser-check that exact final `index.html` again. Explicitly accept that same directory:

   ```bash
   python scripts/accept_progress.py --repo . --report .progress/report-<head> --state .progress/state.json
   ```

## Evidence Rules

- Keep `changed`, `verified`, `blocked`, and `not_observed` visually and semantically distinct.
- Reject stale evidence: `producedAtRef` must equal the report head unless the item is explicitly historical.
- A verified claim requires linked evidence; command evidence must have `exitCode: 0`.
- Sanitize manifest text. Never claim pixels are sanitized. A capture requires `reviewed: true`, reviewer provenance, and useful alt text.
- Visual comparisons may reference only reviewed capture evidence. Historical captures may use `worktreeFingerprint: unknown` only together with `historical: true`; they remain contextual and cannot alone verify current work.
- Use local reviewed images only, referenced by a POSIX-relative path under the manifest directory (no backslashes, drive letters, or leading `/`). The bytes must actually be PNG, JPEG, or WebP. Do not interpolate manifest data into scripts, styles, raw HTML, links, or unquoted attributes.
- Keep collection bounded and show omissions in the report rather than hiding truncation.
- Do not run remote deployments, publish externally, or broaden access merely to obtain evidence.
- Never advance the accepted baseline when collection, rendering, QA, or acceptance fails.
- A truthfully observed blocked project outcome may advance the baseline when it has a blocked claim, fresh evidence, and passed QA. This records progress without calling the outcome successful; pipeline failure or an incomplete report never advances it.

Default to an ignored `.progress/` directory after checking repository policy. Publish under `docs/` only when the user explicitly requests a versioned report.

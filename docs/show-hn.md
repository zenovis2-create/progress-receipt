# Show HN launch copy

## Title

Show HN: progress-receipt – receipts for AI coding

## Body

I built progress-receipt to put the evidence boundary of AI coding work in one reviewable artifact.
It turns a bounded Git range into an HTML report with provenance-labeled change inventory, claims, command results, reviewed captures, blockers, and explicit gaps.
The four claim states—`changed`, `verified`, `blocked`, and `not_observed`—stay separate, and a verified report can still disclose a blocked claim.
The runtime is Python 3.10+ and standard-library only; the checkout demo runs the real collect → render → accept path against an isolated canned Git fixture.
Try it with `uvx --from . progress-receipt demo`; stdout is one absolute report path.

## Likely comments and replies

### “Isn’t this just CI plus a prettier diff?”

CI tells you whether configured jobs exited successfully. This also records a bounded Git inventory, provenance-labeled claims, explicit blockers and gaps, reviewed captures, and integrity hashes in one report—and still does not claim formal correctness.

### “How can a report be verified if one of its claims is blocked?”

Report status applies to the evidence package, not as an aggregate of claim statuses. `verified` means the presented evidence cleared the contract; a fresh, truthfully evidenced blocker can remain visible.

### “Are you claiming the report cannot leak secrets?”

No. Text sanitization targets documented patterns on a best-effort basis, and image pixels are not machine-sanitized; every published capture requires a recorded agent or human review.

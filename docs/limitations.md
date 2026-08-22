# Limitations

`progress-receipt` makes evidence boundaries visible; it cannot remove them.

- Exit code zero shows that a command completed successfully. It does not prove that its assertions cover the intended behavior or that the result is semantically correct.
- Agent-authored before/after statements, impact descriptions, and claims are interpretations, not independent verification. Their provenance remains visible for that reason.
- Text sanitization is best-effort. It targets common secret assignments, secret flags, bearer tokens, private-key markers, private and reserved addresses, control characters, and home paths; it is not a secrecy guarantee.
- Screenshots and other captures are not automatically sanitized. A named agent or human must review them, but that review can miss visible secrets or personal information.
- `verified` means a claim is linked to fresh evidence that passed the report contract. It does not mean formally verified, production-safe, accessible, secure, or correct on platforms that were not observed.
- Bounded Git inventories may omit files or commits. The report discloses those omissions, and reviewers should raise the bounds or inspect the source range when needed.
- Integrity receipts detect changes made after rendering. They do not authenticate the original author or protect a report whose directory and receipt are replaced together by an attacker.

# Limitations

`progress-receipt` makes evidence boundaries visible; it cannot remove them.

- Exit code zero shows that a command completed successfully. It does not prove that its assertions cover the intended behavior or that the result is semantically correct.
- Agent-authored before/after statements, impact descriptions, and claims are interpretations, not independent verification. Acceptance re-derives machine facts — the Git range, inventory, refs, worktree, and file hashes — and never checks whether the narrative is true.
- Text sanitization is best-effort. It targets secret assignments and flags (`api_key=`, `--token`, and similar), `Bearer` and `Basic` authorization values, credentials embedded in URLs, private-key markers, a fixed list of vendor credential prefixes (`sk-ant-`, `gh*_`, `github_pat_`, `glpat-`, `xox*-`, `AKIA`/`ASIA`, `AIza`, `npm_`, JWTs), private and reserved IPv4/IPv6 literals, control characters, and home paths. It does not detect unrecognized formats, high-entropy strings with no key name, or secrets implied by context, and it is not a secrecy guarantee.
- Sanitization is deliberately aggressive about assignment-shaped text: an ordinary line such as `tokens = load()` is redacted because the tool cannot tell it from a real credential.
- Screenshots and other captures are not automatically sanitized. The renderer checks that a capture really is a PNG, JPEG, or WebP file, but nothing inspects its pixels. A named agent or human must review them, and that review can miss visible secrets or personal information.
- `verified` means a claim is linked to fresh evidence that passed the report contract. It does not mean formally verified, production-safe, accessible, secure, or correct on platforms that were not observed.
- The worktree fingerprint covers Git's view of the tree: porcelain status, staged blob IDs, and the content of modified and untracked files. It does not cover ignored files, files outside the repository, submodule contents, or environment state, so evidence can still have been produced under conditions the fingerprint cannot see.
- Bounded Git inventories may omit files or commits. The report discloses those omissions, and reviewers should raise the bounds or inspect the source range when needed.
- Integrity receipts detect changes made after rendering. They do not authenticate the original author or protect a report whose directory and receipt are replaced together by an attacker.
- Acceptance requires the report's baseline to be the branch's last accepted head. After a rebase or force-push that ref is gone; `accept --reset-baseline` is the supported way forward and records the abandoned ref as `divergedFrom` rather than hiding the rewrite.
- Repository identity is derived from the set of root commits reachable from HEAD. Merging in an unrelated history changes that identity, and the branch's accepted baseline will no longer be found under the new key.
- `progress-receipt demo` writes its report to a temporary directory that it does not clean up; pass `--output DIR` to control where it lands.

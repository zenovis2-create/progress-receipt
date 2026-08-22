# Threat model

## What the tool protects

The collector reduces accidental disclosure in manifest text, the renderer prevents manifest values from becoming executable HTML, and the acceptance step detects post-render changes to the report’s HTML, manifest, and reviewed assets. Evidence paths must be local images resolved beneath the manifest directory. Published assets are renamed from safe evidence IDs and hashed.

The report also protects review semantics: current verified and blocked claims must use evidence from the report head and matching worktree fingerprint; successful claims cannot rely on failed command evidence; accepted state advances only after integrity, browser-QA, Git-range, branch, and baseline checks pass.

## Trust boundaries

- Git supplies commit, range, file, and worktree facts.
- Commands supply exit codes, but the report author chooses which commands to run and how to interpret them.
- Agents and humans author narrative claims and review screenshots.
- The local filesystem supplies capture bytes and published report files.

## Out of scope

Sanitization does not discover every credential format, infer secrets from context, or inspect pixels. A malicious report author can omit relevant checks, write misleading narrative, or replace an entire report and receipt together. The project does not sign artifacts, attest execution environments, sandbox commands, upload reports, or grant remote access. Review reports before publication and treat sensitive repositories as sensitive even after sanitization.

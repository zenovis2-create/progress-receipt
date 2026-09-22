# Publication status and follow-ups

## Configured delivery paths

- Hosted CI: Ubuntu, macOS, and Windows × Python 3.10–3.13. Check the [actual runs](https://github.com/zenovis2-create/progress-receipt/actions/workflows/ci.yml) for a specific commit; a configured matrix is not itself a passing result.
- GitHub Releases: tags build/test the wheel and sdist, then publish them as [release assets](https://github.com/zenovis2-create/progress-receipt/releases). The v0.2.0 README includes a pinned wheel installation command.
- GitHub Pages: enabled from `main` at the repository root. The [synthetic review demo](https://zenovis2-create.github.io/progress-receipt/examples/summary-demo/index.html) and [historical self-report](https://zenovis2-create.github.io/progress-receipt/examples/self-report/index.html) are separate artifacts. The new demo does not certify release readiness; the old self-report stays sealed and unchanged.

## Still requiring setup or external verification

- **PyPI:** the public project endpoint returned 404 and no repository `PYPI_TOKEN` secret was configured during release preparation. The release workflow explicitly skips PyPI when the token is absent; GitHub release success must not be reported as PyPI publication. Configure an appropriately scoped PyPI API token (or deliberately migrate to Trusted Publishing with the corresponding PyPI publisher registration) before attempting registry publication. Never put credentials in source or release notes.
- Verify the npm-hosted `npx skills@latest add` installation flow against the public repository and add a short installation capture.
- Measure actual PR-summary review accuracy and evidence-finding time. CI/demo success is not user validation.

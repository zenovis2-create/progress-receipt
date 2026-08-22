# Launch follow-ups

These publication tasks require external accounts or hosted settings, so they cannot be completed or verified from a local checkout:

- Push the current launch-prep commits and confirm the CI matrix (Ubuntu, macOS, Windows × Python 3.10–3.13) on hosted runners. The self-report keeps that hosted-runner verification outside its local scope.
- Publish the v0.1.0 distribution to PyPI so the bare `uvx progress-receipt demo` command resolves publicly.
- Enable GitHub Pages and publish `examples/self-report/` at the project's Pages URL.
- Verify the npm-hosted `npx skills@latest add` flow against the public repository and add a short installation capture to the docs.

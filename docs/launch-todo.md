# Launch follow-ups

These publication tasks require external accounts or hosted settings, so they cannot be completed or verified from a local checkout:

- Push the repository and confirm the CI matrix (Ubuntu, macOS, Windows × Python 3.10–3.13) actually passes on hosted runners. Until then the self-report records that matrix as `not_observed`.
- Install a Python 3.10 interpreter somewhere the self-report generator can reach, so the declared `requires-python` floor stops being recorded as `blocked`.
- Record and embed a short GIF showing `progress-receipt demo`, opening the report, and inspecting linked evidence.
- Publish the v0.1.0 distribution to PyPI so the bare `uvx progress-receipt demo` command resolves publicly.
- Enable GitHub Pages and publish `examples/self-report/` at the project's Pages URL.
- Verify the npm-hosted `npx skills@latest add` flow against the public repository and add a short installation capture to the docs.

"""Packaged entry point for the canonical progress collector."""

# SPDX-License-Identifier: MIT

from __future__ import annotations

from typing import Any

from ._skill_loader import load_skill_script, run_script_cli


_IMPLEMENTATION = load_skill_script("collect_progress")


def main(argv: list[str] | None = None) -> int:
    return run_script_cli("collect_progress", argv)


def __getattr__(name: str) -> Any:
    return getattr(_IMPLEMENTATION, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_IMPLEMENTATION)))


if __name__ == "__main__":
    raise SystemExit(main())

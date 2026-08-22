"""Command-line interface for progress-receipt."""

# SPDX-License-Identifier: MIT

from __future__ import annotations

import argparse
from collections.abc import Sequence

from . import __version__
from . import accept, collect, render
from ._demo import DemoError, build_demo


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="progress-receipt",
        description="Build an evidence-bound, shareable report of project progress.",
    )
    result.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subcommands = result.add_subparsers(dest="command", required=True)
    subcommands.add_parser("collect", add_help=False, help="Collect a bounded Git manifest")
    subcommands.add_parser("render", add_help=False, help="Render and seal a report directory")
    subcommands.add_parser("accept", add_help=False, help="Accept a QA-complete report as baseline")
    subcommands.add_parser("demo", help="Generate a privacy-safe report from a canned repository")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(argv) if argv is not None else None
    args, remainder = parser().parse_known_args(arguments)
    if args.command == "collect":
        return collect.main(remainder)
    if args.command == "render":
        return render.main(remainder)
    if args.command == "accept":
        return accept.main(remainder)
    if remainder:
        parser().error(f"unrecognized arguments: {' '.join(remainder)}")
    try:
        report = build_demo()
    except DemoError as exc:
        raise SystemExit(f"progress demo failed: {exc}") from exc
    print(report.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

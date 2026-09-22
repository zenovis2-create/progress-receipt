"""Command-line interface for progress-receipt."""

# SPDX-License-Identifier: MIT

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys
import webbrowser

from . import __version__
from . import accept, collect, render, summary
from ._demo import DemoError, build_demo, summarize


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
    subcommands.add_parser("summary", add_help=False, help="Export a local PR Markdown summary")
    demo = subcommands.add_parser("demo", help="Generate a privacy-safe report from a canned repository")
    demo.add_argument("--output", help="Write the report directory here instead of a temporary directory")
    demo.add_argument("--open", action="store_true", dest="open_report", help="Open the finished report in the default browser")
    demo.add_argument("--quiet", action="store_true", help="Print only the report path")
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
    if args.command == "summary":
        return summary.main(remainder)
    if remainder:
        parser().error(f"unrecognized arguments: {' '.join(remainder)}")
    try:
        report, manifest = build_demo(Path(args.output) if args.output else None)
    except DemoError as exc:
        raise SystemExit(f"progress demo failed: {exc}") from exc
    report = report.resolve()
    if not args.quiet:
        print(f"progress-receipt {__version__} demo", file=sys.stderr)
        for line in summarize(manifest):
            print(line, file=sys.stderr)
        if not args.output:
            print("  note       written to a temporary directory; use --output DIR to keep it", file=sys.stderr)
        print("\nopen this file in a browser:", file=sys.stderr)
    print(report)
    if args.open_report:
        webbrowser.open(report.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
yaml_validator/cli.py — CLI entry point for yaml-validator.

Usage:
    yaml-validator check <file> [--schema SCHEMA] [flags]
    yaml-validator --help
"""

import sys
from pathlib import Path

from . import __version__
from .validator import validate_file


def main() -> int:
    """CLI entry point for ``yaml-validator``."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="yaml-validator",
        description="Validate YAML files for syntax, style, and schema correctness.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"yaml-validator {__version__}",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # ── check ────────────────────────────────────────────────────────────
    check = sub.add_parser(
        "check",
        help="Validate one or more YAML files.",
        description="Validate one or more YAML files for syntax, style, and schema.",
    )
    check.add_argument(
        "files",
        metavar="FILE",
        nargs="+",
        type=str,
        help="Path(s) to YAML file(s) to validate.",
    )
    check.add_argument(
        "--schema",
        "-s",
        type=str,
        default=None,
        help="Path to a JSON Schema (or YAML schema) file.",
    )
    check.add_argument(
        "--multi",
        "-m",
        action="store_true",
        default=False,
        help="Accept multi-document YAML (``---`` separator).",
    )
    check.add_argument(
        "--no-line-length",
        action="store_false",
        dest="check_line_length",
        default=True,
        help="Disable line-length warnings.",
    )
    check.add_argument(
        "--max-line-length",
        type=int,
        default=200,
        help="Maximum line length before warning (default: 200).",
    )
    check.add_argument(
        "--no-trailing-spaces",
        action="store_false",
        dest="check_trailing_spaces",
        default=True,
        help="Disable trailing-whitespace warnings.",
    )
    check.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        default=False,
        help="Only print errors and final summary.",
    )
    check.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output results as JSON (machine-readable).",
    )

    # ── lint (alias for check) ──────────────────────────────────────────
    lint = sub.add_parser(
        "lint",
        help="Alias for ``check``.",
    )
    lint.add_argument(
        "files",
        metavar="FILE",
        nargs="+",
        type=str,
    )
    lint.add_argument("--schema", "-s", type=str, default=None)
    lint.add_argument("--multi", "-m", action="store_true", default=False)
    lint.add_argument("--quiet", "-q", action="store_true", default=False)
    lint.add_argument("--json", action="store_true", default=False)
    lint.add_argument(
        "--no-line-length",
        action="store_false",
        dest="check_line_length",
        default=True,
    )
    lint.add_argument("--max-line-length", type=int, default=200)
    lint.add_argument(
        "--no-trailing-spaces",
        action="store_false",
        dest="check_trailing_spaces",
        default=True,
    )

    args = parser.parse_args()

    # Normalise: "lint" -> "check"
    command = args.command  # noqa: F841

    all_results = []
    exit_code = 0

    for file_path in args.files:
        p = Path(file_path)
        if not p.exists():
            print(f"Error: file not found — {p}", file=sys.stderr)
            exit_code = 1
            continue

        result = validate_file(
            path=p,
            schema_path=args.schema if getattr(args, "schema", None) else None,
            multi_document=args.multi,
            check_line_length=getattr(args, "check_line_length", True),
            check_trailing_spaces=getattr(args, "check_trailing_spaces", True),
            max_line_length=getattr(args, "max_line_length", 200),
        )

        all_results.append(result)
        if not result.is_valid:
            exit_code = 1

        if args.json:
            _print_json(result)
        else:
            _print_human(result, quiet=args.quiet)

    return exit_code


def _print_human(result, quiet: bool = False) -> None:
    """Print validation result in human-readable format."""
    if result.errors:
        for err in result.errors:
            print(err)
    if result.warnings and not quiet:
        for w in result.warnings:
            print(w)

    if not result.errors and not result.warnings:
        if not quiet:
            print(result.summary)
    else:
        print()
        print(result.summary)


def _print_json(result) -> None:
    """Print validation result as JSON."""
    import json

    payload = {
        "path": result.path,
        "valid": result.is_valid,
        "document_count": result.document_count,
        "errors": [
            {
                "message": e.message,
                "line": e.line,
                "column": e.column,
                "severity": e.severity,
                "rule": e.rule,
            }
            for e in result.errors
        ],
        "warnings": [
            {
                "message": w.message,
                "line": w.line,
                "column": w.column,
                "severity": w.severity,
                "rule": w.rule,
            }
            for w in result.warnings
        ],
    }
    json.dump(payload, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    sys.exit(main())

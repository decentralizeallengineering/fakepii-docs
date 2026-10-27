"""Command line interface: ``fakepii-docs generate | score | verify | schema``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import FORMATS, __version__


def _formats(value: str) -> list[str]:
    items = [v.strip() for v in value.split(",") if v.strip()]
    bad = [v for v in items if v not in FORMATS]
    if bad:
        raise argparse.ArgumentTypeError(f"unknown format(s) {', '.join(bad)}; choose from {', '.join(FORMATS)}")
    return items


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fakepii-docs", description=__doc__)
    p.add_argument("--version", action="version", version=f"fakepii-docs {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    g = sub.add_parser("generate", help="write synthetic documents and their truth files")
    g.add_argument("out_dir", type=Path)
    g.add_argument("--seed", type=int, default=0)
    g.add_argument("--formats", type=_formats, default=list(FORMATS),
                   help=f"comma-separated subset of: {','.join(FORMATS)} (default: all)")
    g.add_argument("--people", type=int, default=3, help="fictional people per document (3-6)")

    s = sub.add_parser("score", help="score a masked document against its truth")
    s.add_argument("original", type=Path)
    s.add_argument("masked", type=Path)
    s.add_argument("truth", type=Path)
    s.add_argument("--unmasked", type=Path, help="document produced by unmasking; checks round-trip fidelity")
    s.add_argument("--json", action="store_true", help="print the full report as JSON")

    v = sub.add_parser("verify", help="scan a document back and check its truth file is exact and complete")
    v.add_argument("document", type=Path)
    v.add_argument("truth", type=Path)

    sub.add_parser("schema", help="print the truth-file JSON Schema")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    # Reports quote document text; never crash on a legacy Windows console code page.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    if args.command == "generate":
        from .corpus import generate_corpus

        manifest = generate_corpus(args.out_dir, args.seed, args.formats, args.people)
        for entry in manifest["documents"]:
            print(f"{entry['format']:<10} {args.out_dir / entry['document']}")
        print(f"manifest   {args.out_dir / 'manifest.json'}")
        return 0
    if args.command == "score":
        from .scorer import format_report, score

        report = score(args.original, args.masked, args.truth, args.unmasked)
        print(json.dumps(report, indent=2, ensure_ascii=False) if args.json else format_report(report))
        return 0 if report["passed"] else 1
    if args.command == "verify":
        from .scan import verify_truth
        from .truth import load_truth

        problems = verify_truth(args.document, load_truth(args.truth))
        for problem in problems:
            print(problem)
        print("OK: truth is exact and complete" if not problems else f"{len(problems)} problem(s)")
        return 0 if not problems else 1
    if args.command == "schema":
        from .truth import load_schema

        print(json.dumps(load_schema(), indent=2))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())

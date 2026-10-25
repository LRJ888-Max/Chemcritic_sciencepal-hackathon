"""Run with python -m app.chemcritic CASE.json --precedents SOURCES.json."""

import argparse
import json
from pathlib import Path
import sys

from .rules import ValidationError, review
from .sciencepal import import_sciencepal
from .sources import load_sources


def main():
    # Use the same encoding for terminal, redirection and subprocess consumers.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="ChemCritic deterministic local review")
    parser.add_argument("case", type=Path)
    sources_group = parser.add_mutually_exclusive_group()
    sources_group.add_argument("--precedents", type=Path)
    sources_group.add_argument("--sciencepal", type=Path, help="Import the observed SM-004 SciencePal JSON export")
    sources_group.add_argument("--library", type=Path, help="Search a normalized team library by exact substrate pair")
    args = parser.parse_args()
    try:
        experiment = json.loads(args.case.read_text(encoding="utf-8-sig"))
        retrieval = None
        if args.library:
            sources, retrieval = load_sources(args.library.read_text(encoding="utf-8-sig"), experiment)
        elif args.sciencepal:
            sources = import_sciencepal(args.sciencepal)
        else:
            sources = json.loads(args.precedents.read_text(encoding="utf-8-sig")) if args.precedents else []
        result = review(experiment, sources)
        if retrieval:
            result["retrieval"].update(retrieval)
    except (OSError, ValueError) as exc:
        error = {"error": "invalid_input", "message": str(exc)}
        if isinstance(exc, ValidationError):
            error["field"] = exc.field
        print(json.dumps(error, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Command-line interface.

    python -m piiredact redact  INPUT.docx OUTPUT.docx [options]
    python -m piiredact scan    INPUT.docx [options]
    python -m piiredact detectors

``redact`` is the deliverable path: it writes the redacted document plus
``audit_log.csv``, ``mapping.csv`` and ``run_summary.json``. ``scan`` is the
same detection with no writing, for checking a change before committing to an
output. ``detectors`` prints the registry, which is the quickest way to confirm
that a newly added PII type is wired up.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import DEFAULT_SPACY_MODEL, Config, DEFAULT_TYPES
from .detectors import iter_registry
from .types import PIIType

_TYPE_NAMES = {t.value.lower(): t for t in PIIType}


def _parse_types(value: str) -> frozenset[PIIType]:
    if value.strip().lower() in ("all", "default"):
        return DEFAULT_TYPES
    wanted: set[PIIType] = set()
    for name in value.split(","):
        name = name.strip().lower()
        if not name:
            continue
        if name not in _TYPE_NAMES:
            raise argparse.ArgumentTypeError(
                f"unknown PII type {name!r}; choose from "
                + ", ".join(sorted(_TYPE_NAMES))
            )
        wanted.add(_TYPE_NAMES[name])
    if not wanted:
        raise argparse.ArgumentTypeError("--types must name at least one PII type")
    return frozenset(wanted)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="piiredact",
        description="Detect and replace PII in a .docx with consistent fake values.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(target: argparse.ArgumentParser) -> None:
        target.add_argument(
            "--types",
            type=_parse_types,
            default=DEFAULT_TYPES,
            metavar="LIST",
            help="comma-separated PII types, or 'all' (default: all)",
        )
        target.add_argument(
            "--seed",
            default=Config.seed,
            help="surrogate seed; the same seed reproduces the same fake values",
        )
        target.add_argument(
            "--locale", default=Config.locale, help="Faker locale for names/places"
        )
        target.add_argument(
            "--min-confidence",
            type=float,
            default=Config.min_confidence,
            metavar="X",
            help="drop detections below this confidence (default: %(default)s)",
        )
        target.add_argument(
            "--spacy-model",
            default=DEFAULT_SPACY_MODEL,
            help="spaCy model for the NER strategy (default: %(default)s)",
        )
        target.add_argument(
            "--no-ner",
            action="store_true",
            help="disable the spaCy NER strategy entirely",
        )
        target.add_argument(
            "--no-sweep",
            action="store_true",
            help="disable the pass-2 gazetteer sweep (ablation)",
        )
        target.add_argument(
            "--dob-mode",
            choices=("labelled", "contextual"),
            default=Config.dob_mode,
            help="date-of-birth strictness (default: %(default)s)",
        )
        target.add_argument(
            "--disable",
            default="",
            metavar="NAMES",
            help="comma-separated detector names to switch off",
        )
        target.add_argument("--quiet", action="store_true", help="suppress progress output")

    redact = sub.add_parser("redact", help="write a redacted copy of a .docx")
    redact.add_argument("input", type=Path)
    redact.add_argument("output", type=Path)
    redact.add_argument(
        "--report-dir",
        type=Path,
        default=None,
        help="where to write the audit trail (default: alongside the output)",
    )
    redact.add_argument(
        "--keep-metadata",
        action="store_true",
        help="do not blank the document's author/company properties",
    )
    add_common(redact)

    scan = sub.add_parser("scan", help="detect PII and print a summary; write nothing")
    scan.add_argument("input", type=Path)
    scan.add_argument(
        "--json", action="store_true", help="print the summary as JSON instead of text"
    )
    scan.add_argument(
        "--show",
        type=int,
        default=0,
        metavar="N",
        help="also print the first N detections",
    )
    add_common(scan)

    sub.add_parser("detectors", help="list the registered detectors")
    return parser


def _config_from(args: argparse.Namespace) -> Config:
    return Config(
        types=args.types,
        seed=args.seed,
        locale=args.locale,
        min_confidence=args.min_confidence,
        spacy_model=None if args.no_ner else args.spacy_model,
        enable_gazetteer_sweep=not args.no_sweep,
        dob_mode=args.dob_mode,
        clear_metadata=not getattr(args, "keep_metadata", False),
        disabled_detectors=frozenset(
            name.strip() for name in args.disable.split(",") if name.strip()
        ),
    )


def _cmd_detectors() -> int:
    print(f"{'pass':>4}  {'detector':28}  types")
    print(f"{'-' * 4}  {'-' * 28}  {'-' * 40}")
    for name, detector in iter_registry():
        types = ", ".join(str(t) for t in detector.pii_types)
        print(f"{detector.pass_number:>4}  {name:28}  {types}")
    return 0


def _cmd_scan(args: argparse.Namespace) -> int:
    from .docio import read_document
    from .pipeline import detect_spans, load_nlp

    config = _config_from(args)
    read = read_document(str(args.input))
    nlp = load_nlp(config.spacy_model)
    result = detect_spans(read.segments, config, nlp=nlp)

    payload = {
        "input": str(args.input),
        "document": read.stats(),
        "total_mentions": result.total_spans,
        "by_type": dict(sorted(result.type_counts.items())),
        "by_detector": dict(sorted(result.detector_contribution.items())),
        "distinct_entities": len(result.gazetteer),
        "resolution": result.resolution.as_dict(),
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"{args.input.name}: {read.stats()['segments']:,} segments")
        print(f"  {result.total_spans:,} PII mentions, "
              f"{len(result.gazetteer):,} distinct entities")
        for pii_type, count in sorted(result.type_counts.items()):
            print(f"    {pii_type:14} {count:>6,}")
    if args.show:
        segments = {s.key: s for s in read.segments}
        shown = 0
        print("\nfirst detections:")
        for key, spans in result.spans_by_segment.items():
            for span in spans:
                print(
                    f"  {segments[key].location:34} {str(span.pii_type):14}"
                    f" {span.confidence:.2f}  {span.text[:60]!r}"
                )
                shown += 1
                if shown >= args.show:
                    return 0
    return 0


def _cmd_redact(args: argparse.Namespace) -> int:
    from .pipeline import redact_document

    config = _config_from(args)
    progress = None if args.quiet else (lambda message: print(message, flush=True))
    result = redact_document(
        args.input,
        args.output,
        config,
        report_dir=args.report_dir,
        progress=progress,
    )
    if not args.quiet:
        print("\nSummary")
        for pii_type, count in sorted(result.detection.type_counts.items()):
            print(f"  {pii_type:14} {count:>6,}")
        print(f"  {'distinct':14} {len(result.surrogate_mapping):>6,} surrogates")
        print(f"\nWrote {result.output_path}")
        report_dir = Path(args.report_dir) if args.report_dir else result.output_path.parent
        print(f"Audit trail in {report_dir}")
    if result.leaks:
        print(
            f"WARNING: {len(result.leaks)} original values still present in the output",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "detectors":
        return _cmd_detectors()
    if args.command == "scan":
        return _cmd_scan(args)
    if args.command == "redact":
        return _cmd_redact(args)
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

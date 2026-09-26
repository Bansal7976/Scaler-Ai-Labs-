"""The pipeline: document in, redacted document plus audit trail out.

    read -> NER pre-pass -> detect (pass 1) -> detect (pass 2) -> resolve
         -> generate surrogates -> write runs -> verify -> report

Each stage is a function here and owns exactly one job, so any of them can be
exercised on its own in the tests and in the evaluation harness. In particular
:func:`detect_spans` is the whole detection stack with no document I/O, which is
what the gold-standard evaluation calls -- the numbers in the evaluation report
come from the same code path the redaction uses, not from a parallel
implementation.

Outputs, all written next to the redacted document:

``audit_log.csv``     one row per replacement: location, type, detector,
                      confidence, original value, surrogate.
``mapping.csv``       the distinct real-to-fake table.
``run_summary.json``  config, counts per type, per-detector contribution,
                      resolver statistics, integrity check results, timings.
"""

from __future__ import annotations

import csv
import json
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from .config import Config, DEFAULT_CONFIG
from .detectors import DetectionContext, Gazetteer, all_detectors
from .detectors.base import PASS_GAZETTEER, PASS_LOCAL
from .docio import (
    ReadResult,
    Replacement,
    SegmentHandle,
    WriteStats,
    apply_document,
    clear_metadata,
    read_document,
    save_document,
    verify_applied,
)
from .resolve import ResolutionStats, resolve
from .surrogates import SurrogateEngine
from .types import PIIType, Segment, Span

#: Entity labels worth caching from spaCy; the rest are never consulted.
_NER_LABELS = frozenset({"PERSON", "ORG", "GPE", "LOC", "FAC", "DATE", "NORP"})
_NER_BATCH_SIZE = 64


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
@dataclass
class DetectionResult:
    """Detection output for a whole document, before any text is rewritten."""

    spans_by_segment: dict[str, list[Span]] = field(default_factory=dict)
    gazetteer: Gazetteer = field(default_factory=Gazetteer)
    resolution: ResolutionStats = field(default_factory=ResolutionStats)
    #: detector name -> spans it contributed that survived resolution
    detector_contribution: Counter[str] = field(default_factory=Counter)
    #: PII type -> surviving span count
    type_counts: Counter[str] = field(default_factory=Counter)
    timings: dict[str, float] = field(default_factory=dict)

    def all_spans(self) -> list[tuple[str, Span]]:
        return [
            (key, span)
            for key, spans in self.spans_by_segment.items()
            for span in spans
        ]

    @property
    def total_spans(self) -> int:
        return sum(len(spans) for spans in self.spans_by_segment.values())


@dataclass
class RedactionResult:
    """Everything one full run produced."""

    detection: DetectionResult
    write_stats: WriteStats
    surrogate_mapping: list[dict[str, str]]
    output_path: Path
    document_stats: dict[str, int]
    cleared_metadata: dict[str, str]
    leaks: list[dict[str, str]]
    config: Config
    timings: dict[str, float]

    def summary(self) -> dict[str, object]:
        return {
            "config": self.config.as_dict(),
            "input_document": self.document_stats,
            "detection": {
                "total_spans": self.detection.total_spans,
                "by_type": dict(sorted(self.detection.type_counts.items())),
                "by_detector": dict(sorted(self.detection.detector_contribution.items())),
                "distinct_entities": len(self.detection.gazetteer),
                "resolution": self.detection.resolution.as_dict(),
            },
            "replacement": self.write_stats.as_dict(),
            "distinct_surrogates": len(self.surrogate_mapping),
            "cleared_metadata_fields": sorted(self.cleared_metadata),
            "integrity": {
                "residual_original_values": len(self.leaks),
                "examples": self.leaks[:20],
            },
            "timings_seconds": {k: round(v, 3) for k, v in self.timings.items()},
            "output": str(self.output_path),
        }


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------
def load_nlp(model: str | None):
    """Load the spaCy pipeline, or return None when NER is disabled."""
    if not model:
        return None
    import spacy

    # The parser and lemmatizer are not used by any detector; excluding them is
    # roughly a 3x speed-up over 3,700 segments.
    return spacy.load(model, exclude=["lemmatizer", "parser"])


def run_ner(segments: Sequence[Segment], nlp) -> dict[str, list[tuple[int, int, str, str]]]:
    """Pre-compute NER entities for every segment, keyed by segment key."""
    cache: dict[str, list[tuple[int, int, str, str]]] = {}
    if nlp is None:
        return cache
    keys = [segment.key for segment in segments]
    texts = [segment.text for segment in segments]
    for key, doc in zip(keys, nlp.pipe(texts, batch_size=_NER_BATCH_SIZE)):
        entities = [
            (ent.start_char, ent.end_char, ent.text, ent.label_)
            for ent in doc.ents
            if ent.label_ in _NER_LABELS
        ]
        if entities:
            cache[key] = entities
    return cache


def _active_detectors(config: Config):
    """Detectors enabled by this config, split by pass."""
    wanted_types = set(config.types)
    active = []
    for detector in all_detectors():
        if detector.name in config.disabled_detectors:
            continue
        if detector.pass_number == PASS_GAZETTEER and not config.enable_gazetteer_sweep:
            continue
        if not wanted_types.intersection(detector.pii_types):
            continue
        active.append(detector)
    return active


def detect_spans(
    segments: Sequence[Segment],
    config: Config = DEFAULT_CONFIG,
    *,
    nlp=None,
    ner_cache: dict[str, list[tuple[int, int, str, str]]] | None = None,
) -> DetectionResult:
    """Run the full detection stack over ``segments``.

    This is the function the evaluation harness calls, so the measured system and
    the shipped system cannot drift apart.
    """
    result = DetectionResult()
    wanted = set(config.types)
    detectors = _active_detectors(config)

    started = time.perf_counter()
    if ner_cache is None:
        ner_cache = run_ner(segments, nlp)
    result.timings["ner"] = time.perf_counter() - started

    ctx = DetectionContext(
        gazetteer=result.gazetteer,
        nlp=nlp,
        segments=segments,
        ner_cache=ner_cache,
        min_confidence=config.min_confidence,
    )
    # Configure detectors that take options from the config.
    for detector in detectors:
        if getattr(detector, "name", "") == "dob.contextual":
            detector.mode = config.dob_mode  # type: ignore[attr-defined]

    raw: dict[str, list[Span]] = defaultdict(list)
    for pass_number in (PASS_LOCAL, PASS_GAZETTEER):
        pass_detectors = [d for d in detectors if d.pass_number == pass_number]
        if not pass_detectors:
            continue
        started = time.perf_counter()
        for detector in pass_detectors:
            prepare = getattr(detector, "prepare", None)
            if callable(prepare):
                prepare(ctx)
        for segment in segments:
            for detector in pass_detectors:
                for span in detector.detect(segment, ctx):
                    if span.pii_type in wanted:
                        raw[segment.key].append(span)
        result.timings[f"pass{pass_number}"] = time.perf_counter() - started

    started = time.perf_counter()
    text_by_key = {segment.key: segment.text for segment in segments}
    for key, spans in raw.items():
        resolved = resolve(
            spans,
            text_by_key[key],
            min_confidence=config.min_confidence,
            stats=result.resolution,
        )
        if resolved:
            result.spans_by_segment[key] = resolved
            for span in resolved:
                result.detector_contribution[span.detector] += 1
                result.type_counts[str(span.pii_type)] += 1
    result.timings["resolve"] = time.perf_counter() - started
    return result


def build_replacements(
    detection: DetectionResult, engine: SurrogateEngine
) -> dict[str, list[Replacement]]:
    """Attach a surrogate to every resolved span."""
    plan: dict[str, list[Replacement]] = {}
    for key, spans in detection.spans_by_segment.items():
        plan[key] = [Replacement(span, engine.surrogate_for(span)) for span in spans]
    return plan


def redact_document(
    input_path: str | Path,
    output_path: str | Path,
    config: Config = DEFAULT_CONFIG,
    *,
    report_dir: str | Path | None = None,
    progress=None,
) -> RedactionResult:
    """Redact one .docx end to end and write the audit trail."""
    input_path = Path(input_path)
    output_path = Path(output_path)
    report_dir = Path(report_dir) if report_dir else output_path.parent
    report_dir.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}

    def say(message: str) -> None:
        if progress is not None:
            progress(message)

    started = time.perf_counter()
    say(f"Reading {input_path.name}")
    read = read_document(str(input_path))
    document_stats = read.stats()
    timings["read"] = time.perf_counter() - started
    say(
        f"  {document_stats['segments']:,} segments, "
        f"{document_stats['runs']:,} runs, "
        f"{document_stats['characters']:,} characters"
    )

    started = time.perf_counter()
    say(f"Loading NER model ({config.spacy_model or 'disabled'})")
    nlp = load_nlp(config.spacy_model)
    timings["load_model"] = time.perf_counter() - started

    say("Detecting PII")
    detection = detect_spans(read.segments, config, nlp=nlp)
    timings.update(detection.timings)
    say(
        f"  {detection.total_spans:,} mentions across "
        f"{len(detection.type_counts)} types, "
        f"{len(detection.gazetteer):,} distinct entities"
    )

    started = time.perf_counter()
    say("Generating surrogates")
    engine = SurrogateEngine(
        gazetteer=detection.gazetteer, seed=config.seed, locale=config.locale
    )
    plan = build_replacements(detection, engine)
    timings["surrogates"] = time.perf_counter() - started

    started = time.perf_counter()
    say("Rewriting document")
    handles = read.by_key()
    write_stats = apply_document(handles, plan)
    cleared = clear_metadata(read.document) if config.clear_metadata else {}
    save_document(read.document, str(output_path))
    timings["write"] = time.perf_counter() - started
    say(f"  {write_stats.replacements_applied:,} replacements written")

    leaks: list[dict[str, str]] = []
    if config.verify_output:
        started = time.perf_counter()
        say("Verifying output")
        leaks = _verify(handles, plan)
        timings["verify"] = time.perf_counter() - started
        say(f"  {len(leaks)} residual original values")

    result = RedactionResult(
        detection=detection,
        write_stats=write_stats,
        surrogate_mapping=engine.mapping(),
        output_path=output_path,
        document_stats=document_stats,
        cleared_metadata=cleared,
        leaks=leaks,
        config=config,
        timings=timings,
    )
    _write_reports(result, read, plan, report_dir)
    return result


def _verify(
    handles: dict[str, SegmentHandle], plan: dict[str, Sequence[Replacement]]
) -> list[dict[str, str]]:
    """Re-read every rewritten segment and report surviving original values."""
    leaks: list[dict[str, str]] = []
    for key, replacements in plan.items():
        handle = handles.get(key)
        if handle is None:  # pragma: no cover
            continue
        for value in verify_applied(handle, replacements):
            leaks.append({"segment": handle.segment.location, "value": value})
    return leaks


def _write_reports(
    result: RedactionResult,
    read: ReadResult,
    plan: dict[str, Sequence[Replacement]],
    report_dir: Path,
) -> None:
    handles = read.by_key()
    audit_path = report_dir / "audit_log.csv"
    with audit_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "location",
                "section",
                "column_header",
                "pii_type",
                "detector",
                "confidence",
                "start",
                "end",
                "original",
                "surrogate",
            ]
        )
        for key, replacements in plan.items():
            segment = handles[key].segment
            for replacement in sorted(replacements, key=lambda r: r.span.start):
                span = replacement.span
                writer.writerow(
                    [
                        segment.location,
                        segment.section or "",
                        segment.column_header or "",
                        str(span.pii_type),
                        span.detector,
                        f"{span.confidence:.2f}",
                        span.start,
                        span.end,
                        span.text,
                        replacement.surrogate,
                    ]
                )

    mapping_path = report_dir / "mapping.csv"
    with mapping_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["pii_type", "original", "surrogate"])
        writer.writeheader()
        writer.writerows(result.surrogate_mapping)

    summary_path = report_dir / "run_summary.json"
    summary_path.write_text(
        json.dumps(result.summary(), indent=2, ensure_ascii=False), encoding="utf-8"
    )


__all__ = [
    "DetectionResult",
    "RedactionResult",
    "build_replacements",
    "detect_spans",
    "load_nlp",
    "redact_document",
    "run_ner",
]

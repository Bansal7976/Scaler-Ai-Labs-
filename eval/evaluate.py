"""Scoring harness: gold standard in, precision/recall/F1/accuracy out.

Three things about the design are worth stating, because they are what makes the
numbers in ``EVALUATION_REPORT.md`` mean something.

**It calls the shipped code.** Scoring runs
:func:`piiredact.pipeline.detect_spans` on the sample segments -- the same
function the redaction uses. There is no reimplementation to drift out of sync.

**It reports two matching criteria, not one.**

*Strict* requires the predicted span to have the same type and exactly the same
character boundaries as the gold span. It is the right measure for a value that
will be *replaced*: getting the boundary wrong means writing a surrogate over the
wrong characters.

*Relaxed* (the MUC-style partial credit used in most NER evaluations) requires
the same type and any character overlap. It is the right measure for *privacy*:
an address whose span is two words short still had its identifying content
replaced.

Reporting only strict understates a redactor's protective value; reporting only
relaxed hides real boundary bugs. Both are here, per type.

**It also reports character-level accuracy**, because the assignment asks for
accuracy and a span task has no natural accuracy. Every character of every
sampled segment is one instance, labelled PII or not-PII, which makes accuracy
well defined -- and heavily dominated by the ~95% of characters that are not PII.
That is why accuracy is reported alongside, and never instead of, precision and
recall.

Usage::

    python eval/evaluate.py                 # full run, writes eval/results/
    python eval/evaluate.py --no-ablations   # faster
    python eval/evaluate.py --errors 40      # print more error detail
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))

from piiredact.config import Config  # noqa: E402
from piiredact.pipeline import detect_spans, load_nlp  # noqa: E402
from piiredact.types import PIIType, Segment, Span  # noqa: E402

GOLD_PATH = HERE / "gold_standard.jsonl"
SYNTHETIC_PATH = ROOT / "tests" / "fixtures" / "synthetic_corpus.jsonl"
RESULTS_DIR = HERE / "results"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
@dataclass
class GoldSegment:
    key: str
    text: str
    location: str
    stratum: str
    spans: list[tuple[str, int, int, str]]
    column_header: str | None = None
    kind: str = "paragraph"
    table_index: int | None = None
    row_index: int | None = None
    col_index: int | None = None

    def to_segment(self) -> Segment:
        return Segment(
            key=self.key,
            text=self.text,
            location=self.location,
            kind=self.kind,
            column_header=self.column_header,
            table_index=self.table_index,
            row_index=self.row_index,
            col_index=self.col_index,
        )


def load_gold(path: Path, sample_path: Path | None = None) -> list[GoldSegment]:
    """Load a gold file, re-attaching the structural fields from the sample.

    The table column header is part of the *input* the detectors see, so it must
    be restored here or the structural strategies would be evaluated with one
    hand tied behind their back.
    """
    structure: dict[str, dict] = {}
    if sample_path and sample_path.exists():
        sample = json.loads(sample_path.read_text(encoding="utf-8"))
        structure = {s["key"]: s for s in sample["segments"]}

    out: list[GoldSegment] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        meta = structure.get(row["key"], {})
        location = row["location"]
        table_index = row_index = col_index = None
        if meta.get("kind") == "table-cell":
            parts = location.replace(",", "").split()
            try:
                table_index = int(parts[1])
                row_index = int(parts[3])
                col_index = int(parts[5])
            except (IndexError, ValueError):  # pragma: no cover
                pass
        out.append(
            GoldSegment(
                key=row["key"],
                text=row["text"],
                location=location,
                stratum=row.get("stratum", "unknown"),
                spans=[
                    (s["pii_type"], s["start"], s["end"], s["text"])
                    for s in row["spans"]
                ],
                column_header=meta.get("column_header"),
                kind=meta.get("kind", "paragraph"),
                table_index=table_index,
                row_index=row_index,
                col_index=col_index,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
@dataclass
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    @property
    def support(self) -> int:
        return self.tp + self.fn

    def as_dict(self) -> dict[str, float | int]:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "support": self.support,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
        }

    def __iadd__(self, other: "Counts") -> "Counts":
        self.tp += other.tp
        self.fp += other.fp
        self.fn += other.fn
        return self


@dataclass
class Errors:
    false_positives: list[dict] = field(default_factory=list)
    false_negatives: list[dict] = field(default_factory=list)
    #: (gold type, predicted type) -> count, for spans that overlap but disagree
    type_confusions: Counter = field(default_factory=Counter)
    #: gold spans matched only partially, with the predicted text
    boundary_errors: list[dict] = field(default_factory=list)


def _match(
    gold: Sequence[tuple[str, int, int, str]],
    predicted: Sequence[Span],
    *,
    strict: bool,
) -> tuple[list[tuple[int, int]], set[int], set[int]]:
    """Greedy one-to-one matching. Returns (pairs, unmatched_gold, unmatched_pred)."""
    candidates: list[tuple[int, int, int]] = []  # (-overlap, gold_i, pred_i)
    for gi, (gtype, gstart, gend, _) in enumerate(gold):
        for pi, span in enumerate(predicted):
            if str(span.pii_type) != gtype:
                continue
            if strict:
                if span.start == gstart and span.end == gend:
                    candidates.append((-(gend - gstart), gi, pi))
            else:
                overlap = min(gend, span.end) - max(gstart, span.start)
                if overlap > 0:
                    candidates.append((-overlap, gi, pi))
    candidates.sort()
    used_gold: set[int] = set()
    used_pred: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for _, gi, pi in candidates:
        if gi in used_gold or pi in used_pred:
            continue
        used_gold.add(gi)
        used_pred.add(pi)
        pairs.append((gi, pi))
    unmatched_gold = set(range(len(gold))) - used_gold
    unmatched_pred = set(range(len(predicted))) - used_pred
    return pairs, unmatched_gold, unmatched_pred


def score(
    segments: Sequence[GoldSegment],
    predictions: dict[str, list[Span]],
    *,
    strict: bool,
    collect_errors: Errors | None = None,
) -> dict[str, Counts]:
    """Per-type counts under one matching criterion."""
    counts: dict[str, Counts] = defaultdict(Counts)
    for segment in segments:
        gold = segment.spans
        predicted = predictions.get(segment.key, [])
        pairs, missed, spurious = _match(gold, predicted, strict=strict)
        for gi, pi in pairs:
            counts[gold[gi][0]].tp += 1
            if collect_errors is not None and not strict:
                g = gold[gi]
                p = predicted[pi]
                if (p.start, p.end) != (g[1], g[2]):
                    collect_errors.boundary_errors.append(
                        {
                            "location": segment.location,
                            "pii_type": g[0],
                            "gold": g[3],
                            "predicted": p.text,
                        }
                    )
        for gi in sorted(missed):
            counts[gold[gi][0]].fn += 1
            if collect_errors is not None:
                overlapping = [
                    p
                    for p in predicted
                    if p.start < gold[gi][2] and gold[gi][1] < p.end
                ]
                if overlapping and not strict:
                    collect_errors.type_confusions[
                        (gold[gi][0], str(overlapping[0].pii_type))
                    ] += 1
                collect_errors.false_negatives.append(
                    {
                        "location": segment.location,
                        "stratum": segment.stratum,
                        "pii_type": gold[gi][0],
                        "text": gold[gi][3],
                        "overlapping_prediction": (
                            f"{overlapping[0].pii_type}:{overlapping[0].text}"
                            if overlapping
                            else None
                        ),
                    }
                )
        for pi in sorted(spurious):
            span = predicted[pi]
            counts[str(span.pii_type)].fp += 1
            if collect_errors is not None:
                collect_errors.false_positives.append(
                    {
                        "location": segment.location,
                        "stratum": segment.stratum,
                        "pii_type": str(span.pii_type),
                        "text": span.text,
                        "detector": span.detector,
                        "confidence": round(span.confidence, 2),
                    }
                )
    return dict(counts)


def aggregate(counts: dict[str, Counts]) -> dict[str, object]:
    """Micro and macro averages over the per-type counts."""
    micro = Counts()
    for value in counts.values():
        micro += Counts(value.tp, value.fp, value.fn)
    present = [c for c in counts.values() if c.support > 0]
    macro_p = sum(c.precision for c in present) / len(present) if present else 0.0
    macro_r = sum(c.recall for c in present) / len(present) if present else 0.0
    macro_f = sum(c.f1 for c in present) / len(present) if present else 0.0
    return {
        "per_type": {k: v.as_dict() for k, v in sorted(counts.items())},
        "micro": micro.as_dict(),
        "macro": {
            "precision": round(macro_p, 4),
            "recall": round(macro_r, 4),
            "f1": round(macro_f, 4),
            "types_averaged": len(present),
        },
    }


# ---------------------------------------------------------------------------
# Character-level accuracy
# ---------------------------------------------------------------------------
def character_metrics(
    segments: Sequence[GoldSegment], predictions: dict[str, list[Span]]
) -> dict[str, object]:
    """Binary PII / not-PII confusion over every character in the sample."""
    tp = fp = fn = tn = 0
    for segment in segments:
        length = len(segment.text)
        gold_mask = bytearray(length)
        for _, start, end, _ in segment.spans:
            for i in range(start, min(end, length)):
                gold_mask[i] = 1
        pred_mask = bytearray(length)
        for span in predictions.get(segment.key, []):
            for i in range(span.start, min(span.end, length)):
                pred_mask[i] = 1
        for g, p in zip(gold_mask, pred_mask):
            if g and p:
                tp += 1
            elif p and not g:
                fp += 1
            elif g and not p:
                fn += 1
            else:
                tn += 1
    total = tp + fp + fn + tn
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "characters": total,
        "pii_characters": tp + fn,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "accuracy": round((tp + tn) / total, 6) if total else 0.0,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(2 * precision * recall / (precision + recall), 4)
        if precision + recall
        else 0.0,
        "specificity": round(tn / (tn + fp), 6) if tn + fp else 0.0,
    }


# ---------------------------------------------------------------------------
# Segment-level detection (does a segment carrying PII get flagged at all?)
# ---------------------------------------------------------------------------
def segment_metrics(
    segments: Sequence[GoldSegment], predictions: dict[str, list[Span]]
) -> dict[str, object]:
    tp = fp = fn = tn = 0
    for segment in segments:
        has_gold = bool(segment.spans)
        has_pred = bool(predictions.get(segment.key))
        if has_gold and has_pred:
            tp += 1
        elif has_pred and not has_gold:
            fp += 1
        elif has_gold and not has_pred:
            fn += 1
        else:
            tn += 1
    total = tp + fp + fn + tn
    return {
        "segments": total,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "accuracy": round((tp + tn) / total, 4) if total else 0.0,
        "precision": round(tp / (tp + fp), 4) if tp + fp else 0.0,
        "recall": round(tp / (tp + fn), 4) if tp + fn else 0.0,
    }


# ---------------------------------------------------------------------------
# Running the system under evaluation
# ---------------------------------------------------------------------------
def predict(
    segments: Sequence[GoldSegment], config: Config, nlp
) -> dict[str, list[Span]]:
    result = detect_spans([s.to_segment() for s in segments], config, nlp=nlp)
    return {key: list(spans) for key, spans in result.spans_by_segment.items()}


def evaluate_corpus(
    name: str,
    segments: Sequence[GoldSegment],
    config: Config,
    nlp,
    *,
    collect_errors: bool = True,
) -> dict[str, object]:
    predictions = predict(segments, config, nlp)
    errors = Errors() if collect_errors else None
    strict = score(segments, predictions, strict=True)
    relaxed = score(segments, predictions, strict=False, collect_errors=errors)
    payload: dict[str, object] = {
        "corpus": name,
        "segments": len(segments),
        "gold_spans": sum(len(s.spans) for s in segments),
        "predicted_spans": sum(len(v) for v in predictions.values()),
        "strict": aggregate(strict),
        "relaxed": aggregate(relaxed),
        "character_level": character_metrics(segments, predictions),
        "segment_level": segment_metrics(segments, predictions),
        "by_stratum": _by_stratum(segments, predictions),
    }
    if errors is not None:
        payload["errors"] = {
            "false_positives": errors.false_positives,
            "false_negatives": errors.false_negatives,
            "boundary_errors": errors.boundary_errors,
            "type_confusions": {
                f"{g}->{p}": n for (g, p), n in sorted(errors.type_confusions.items())
            },
        }
    return payload


def _by_stratum(
    segments: Sequence[GoldSegment], predictions: dict[str, list[Span]]
) -> dict[str, object]:
    groups: dict[str, list[GoldSegment]] = defaultdict(list)
    for segment in segments:
        groups[segment.stratum].append(segment)
    out: dict[str, object] = {}
    for stratum, members in sorted(groups.items()):
        counts = score(members, predictions, strict=False)
        micro = Counts()
        for value in counts.values():
            micro += Counts(value.tp, value.fp, value.fn)
        out[stratum] = {
            "segments": len(members),
            "gold_spans": sum(len(s.spans) for s in members),
            **micro.as_dict(),
        }
    return out


# ---------------------------------------------------------------------------
# Ablations
# ---------------------------------------------------------------------------
ABLATIONS: tuple[tuple[str, str, Config], ...] = (
    (
        "full",
        "the shipped configuration",
        Config(),
    ),
    (
        "no_gazetteer_sweep",
        "pass 1 only -- structural and NER detection, no document-wide sweep",
        Config(enable_gazetteer_sweep=False),
    ),
    (
        "no_ner",
        "no spaCy; regex, lexicons and table structure only",
        Config(spacy_model=None),
    ),
    (
        "no_ner_no_sweep",
        "structural and regex detection only",
        Config(spacy_model=None, enable_gazetteer_sweep=False),
    ),
    (
        "no_person_detector",
        "the person detector disabled, to isolate what it contributes",
        Config().without("person.hybrid"),
    ),
    (
        "min_confidence_0",
        "every detection kept, however weak",
        Config(min_confidence=0.0),
    ),
    (
        "min_confidence_090",
        "high-confidence detections only",
        Config(min_confidence=0.90),
    ),
)


def run_ablations(segments: Sequence[GoldSegment]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    cache: dict[str | None, object] = {}
    for name, description, config in ABLATIONS:
        if config.spacy_model not in cache:
            cache[config.spacy_model] = load_nlp(config.spacy_model)
        nlp = cache[config.spacy_model]
        predictions = predict(segments, config, nlp)
        relaxed = score(segments, predictions, strict=False)
        strict = score(segments, predictions, strict=True)
        micro_relaxed = Counts()
        for value in relaxed.values():
            micro_relaxed += Counts(value.tp, value.fp, value.fn)
        micro_strict = Counts()
        for value in strict.values():
            micro_strict += Counts(value.tp, value.fp, value.fn)
        rows.append(
            {
                "ablation": name,
                "description": description,
                "predicted_spans": sum(len(v) for v in predictions.values()),
                "relaxed": micro_relaxed.as_dict(),
                "strict": micro_strict.as_dict(),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _fmt_table(rows: Iterable[Sequence[object]], headers: Sequence[str]) -> str:
    rows = [[str(cell) for cell in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    sep = "  ".join("-" * widths[i] for i in range(len(headers)))
    body = "\n".join(
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) for row in rows
    )
    return f"{line}\n{sep}\n{body}"


def print_report(payload: dict[str, object], error_limit: int) -> None:
    print(f"\n{'=' * 78}\n{payload['corpus']}\n{'=' * 78}")
    print(
        f"{payload['segments']} segments, {payload['gold_spans']} gold spans, "
        f"{payload['predicted_spans']} predicted spans"
    )
    for criterion in ("relaxed", "strict"):
        block = payload[criterion]  # type: ignore[index]
        print(f"\n-- {criterion} matching --")
        rows = [
            [
                pii_type,
                stats["support"],
                stats["tp"],
                stats["fp"],
                stats["fn"],
                f"{stats['precision']:.3f}",
                f"{stats['recall']:.3f}",
                f"{stats['f1']:.3f}",
            ]
            for pii_type, stats in block["per_type"].items()  # type: ignore[index]
        ]
        micro = block["micro"]  # type: ignore[index]
        macro = block["macro"]  # type: ignore[index]
        rows.append(
            [
                "MICRO",
                micro["support"],
                micro["tp"],
                micro["fp"],
                micro["fn"],
                f"{micro['precision']:.3f}",
                f"{micro['recall']:.3f}",
                f"{micro['f1']:.3f}",
            ]
        )
        rows.append(
            [
                "MACRO",
                "-",
                "-",
                "-",
                "-",
                f"{macro['precision']:.3f}",
                f"{macro['recall']:.3f}",
                f"{macro['f1']:.3f}",
            ]
        )
        print(
            _fmt_table(
                rows, ["type", "gold", "tp", "fp", "fn", "prec", "rec", "f1"]
            )
        )
    char = payload["character_level"]  # type: ignore[index]
    print(
        f"\n-- character level -- accuracy {char['accuracy']:.4f}  "
        f"precision {char['precision']:.3f}  recall {char['recall']:.3f}  "
        f"f1 {char['f1']:.3f}  ({char['pii_characters']:,} of "
        f"{char['characters']:,} characters are PII)"
    )
    seg = payload["segment_level"]  # type: ignore[index]
    print(
        f"-- segment level    -- accuracy {seg['accuracy']:.4f}  "
        f"precision {seg['precision']:.3f}  recall {seg['recall']:.3f}"
    )
    print("\n-- by stratum (relaxed) --")
    print(
        _fmt_table(
            [
                [
                    stratum,
                    s["segments"],
                    s["gold_spans"],
                    s["tp"],
                    s["fp"],
                    s["fn"],
                    f"{s['precision']:.3f}",
                    f"{s['recall']:.3f}",
                ]
                for stratum, s in payload["by_stratum"].items()  # type: ignore[index]
            ],
            ["stratum", "segs", "gold", "tp", "fp", "fn", "prec", "rec"],
        )
    )
    errors = payload.get("errors")
    if not errors:
        return
    for label, key in (("false positives", "false_positives"), ("misses", "false_negatives")):
        items = errors[key]  # type: ignore[index]
        print(f"\n-- {label} ({len(items)}) --")
        for item in items[:error_limit]:
            extra = item.get("detector") or item.get("overlapping_prediction") or ""
            print(
                f"  {item['location']:<32} {item['pii_type']:<13} "
                f"{item['text'][:56]!r} {extra}"
            )
        if len(items) > error_limit:
            print(f"  ... {len(items) - error_limit} more")
    confusions = errors["type_confusions"]  # type: ignore[index]
    if confusions:
        print("\n-- type confusions --")
        for pair, count in confusions.items():
            print(f"  {pair:<34} {count}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score the redactor against the gold standard.")
    parser.add_argument("--no-ablations", action="store_true")
    parser.add_argument("--errors", type=int, default=25, help="error rows to print")
    parser.add_argument("--json-only", action="store_true")
    args = parser.parse_args(argv)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    config = Config()
    nlp = load_nlp(config.spacy_model)

    document = load_gold(GOLD_PATH, HERE / "sample.json")
    document_result = evaluate_corpus(
        "KSH International RHP -- hand-annotated sample", document, config, nlp
    )

    synthetic = load_gold(SYNTHETIC_PATH)
    synthetic_result = evaluate_corpus(
        "Synthetic corpus -- SSN / card / DOB / IP", synthetic, config, nlp
    )

    payload: dict[str, object] = {
        "config": config.as_dict(),
        "document": document_result,
        "synthetic": synthetic_result,
    }
    if not args.no_ablations:
        payload["ablations"] = run_ablations(document)

    (RESULTS_DIR / "metrics.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    if not args.json_only:
        print_report(document_result, args.errors)
        print_report(synthetic_result, args.errors)
        if "ablations" in payload:
            print(f"\n{'=' * 78}\nAblations (relaxed micro)\n{'=' * 78}")
            print(
                _fmt_table(
                    [
                        [
                            row["ablation"],
                            row["predicted_spans"],
                            row["relaxed"]["tp"],
                            row["relaxed"]["fp"],
                            row["relaxed"]["fn"],
                            f"{row['relaxed']['precision']:.3f}",
                            f"{row['relaxed']['recall']:.3f}",
                            f"{row['relaxed']['f1']:.3f}",
                            f"{row['strict']['f1']:.3f}",
                        ]
                        for row in payload["ablations"]  # type: ignore[index]
                    ],
                    ["ablation", "pred", "tp", "fp", "fn", "prec", "rec", "f1", "strictF1"],
                )
            )
    print(f"\nwrote {RESULTS_DIR / 'metrics.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

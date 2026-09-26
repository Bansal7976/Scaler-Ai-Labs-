"""Overlap resolution: turning many detectors' opinions into one redaction plan.

Twelve detectors run over the same text, and several of them will legitimately
claim the same characters. ``ICICI Securities Limited`` is an organisation to the
suffix detector and, in a slash-separated contact list, could be partly claimed
by the person detector. ``+91 22 6807 7100`` is claimed once by the labelled
phone strategy and again by the country-code strategy.

The resolver reduces those claims to a set of disjoint spans using four rules,
applied in order. They are stated here because they *are* the specification --
the replacement stage assumes its input is disjoint and sorted.

**R1 Confidence floor.** Drop spans below the configured minimum confidence.

**R2 Duplicates.** Identical ``(start, end, type)`` spans collapse to one, taking
the highest confidence and recording every detector that found it. This is how
the audit log can show that two independent strategies agreed.

**R3 Containment.** Claims are considered strongest-first, ordered by type
priority (see :data:`piiredact.types.TYPE_PRIORITY`) and then by length. A claim
wholly inside an already-accepted one is absorbed, so a ``PERSON`` span inside a
longer ``PERSON`` span disappears into it rather than splitting the name.

**R4 Partial overlap, and containment the other way round.** When an accepted
span sits *inside* a later, weaker claim, the weaker claim is truncated to the
characters the accepted span does not use -- an ``EMAIL`` keeps its characters and
the surrounding ``ORGANIZATION`` claim shrinks around it, rather than either being
thrown away. The same truncation applies to ordinary partial overlaps. A
remainder shorter than :data:`MIN_TRUNCATED_LEN`, or one left holding nothing but
punctuation, is dropped rather than replaced as a fragment.

The result is sorted by start offset with no two spans overlapping, which is
exactly what :mod:`piiredact.docio.docx_writer` needs to rewrite runs in place.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Iterable, Sequence

from .types import PIIType, Span

#: A truncated span shorter than this is discarded instead of being replaced.
MIN_TRUNCATED_LEN = 3


@dataclass
class ResolutionStats:
    """What the resolver did, for the audit log and the evaluation report."""

    input_spans: int = 0
    output_spans: int = 0
    dropped_low_confidence: int = 0
    merged_duplicates: int = 0
    dropped_contained: int = 0
    truncated: int = 0
    dropped_after_truncation: int = 0
    #: type -> number of spans surviving
    by_type: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def as_dict(self) -> dict[str, object]:
        return {
            "input_spans": self.input_spans,
            "output_spans": self.output_spans,
            "dropped_low_confidence": self.dropped_low_confidence,
            "merged_duplicates": self.merged_duplicates,
            "dropped_contained": self.dropped_contained,
            "truncated": self.truncated,
            "dropped_after_truncation": self.dropped_after_truncation,
            "by_type": dict(sorted(self.by_type.items())),
        }


def _better(left: Span, right: Span) -> Span:
    """The span that wins a conflict between ``left`` and ``right``."""
    if left.priority != right.priority:
        return left if left.priority < right.priority else right
    if left.length != right.length:
        return left if left.length > right.length else right
    if left.confidence != right.confidence:
        return left if left.confidence > right.confidence else right
    # Final tie-break on detector name so runs are byte-for-byte reproducible.
    return left if left.detector <= right.detector else right


def _merge_duplicates(spans: Sequence[Span], stats: ResolutionStats) -> list[Span]:
    grouped: dict[tuple[int, int, PIIType], list[Span]] = defaultdict(list)
    for span in spans:
        grouped[(span.start, span.end, span.pii_type)].append(span)
    merged: list[Span] = []
    for group in grouped.values():
        if len(group) == 1:
            merged.append(group[0])
            continue
        stats.merged_duplicates += len(group) - 1
        best = max(group, key=lambda s: (s.confidence, s.detector))
        detectors = sorted({s.detector for s in group})
        evidence = dict(best.evidence)
        evidence["agreeing_detectors"] = detectors
        merged.append(replace(best, detector="+".join(detectors), evidence=evidence))
    return merged


def _truncate(span: Span, winner: Span, text: str) -> Span | None:
    """Return ``span`` with ``winner``'s characters removed, or None.

    When ``winner`` sits strictly inside ``span`` the split leaves a remainder on
    each side; the longer one is kept. Emitting both would replace one mention
    with two unrelated surrogates, which reads worse than losing the shorter
    fragment -- and the shorter fragment is nearly always punctuation or an
    article.
    """
    left = (span.start, min(span.end, winner.start))
    right = (max(span.start, winner.end), span.end)
    options = [(s, e) for s, e in (left, right) if e - s >= MIN_TRUNCATED_LEN]
    if not options:
        return None
    start, end = max(options, key=lambda pair: pair[1] - pair[0])
    if end - start < MIN_TRUNCATED_LEN:
        return None
    fragment = text[start:end]
    trimmed = fragment.strip(" \t,;:|.-–—")
    if len(trimmed) < MIN_TRUNCATED_LEN:
        return None
    offset = fragment.index(trimmed)
    start += offset
    end = start + len(trimmed)
    evidence = dict(span.evidence)
    evidence["truncated_by"] = f"{winner.pii_type}@{winner.start}-{winner.end}"
    return replace(span, start=start, end=end, text=text[start:end], evidence=evidence)


def resolve(
    spans: Iterable[Span],
    text: str,
    *,
    min_confidence: float = 0.0,
    stats: ResolutionStats | None = None,
) -> list[Span]:
    """Reduce overlapping detector output to a disjoint, sorted redaction plan."""
    stats = stats if stats is not None else ResolutionStats()
    candidates = list(spans)
    stats.input_spans += len(candidates)

    # R1: confidence floor.
    if min_confidence > 0.0:
        kept = [s for s in candidates if s.confidence >= min_confidence]
        stats.dropped_low_confidence += len(candidates) - len(kept)
        candidates = kept

    # R2: duplicates.
    candidates = _merge_duplicates(candidates, stats)

    # R3/R4: sweep left to right, keeping a frontier of accepted spans.
    # Sorting by (priority, -length) means the strongest claim on any region is
    # considered first, so later claims only ever get truncated or dropped.
    candidates.sort(key=lambda s: (s.priority, -s.length, s.start, s.detector))
    accepted: list[Span] = []
    for span in candidates:
        current: Span | None = span
        for winner in accepted:
            if current is None:
                break
            if not current.overlaps(winner):
                continue
            if winner.contains(current):
                stats.dropped_contained += 1
                current = None
                break
            better = _better(winner, current)
            if better is winner:
                truncated = _truncate(current, winner, text)
                if truncated is None:
                    stats.dropped_after_truncation += 1
                    current = None
                else:
                    stats.truncated += 1
                    current = truncated
            else:  # pragma: no cover - ordering makes this unreachable
                current = None
        if current is not None:
            accepted.append(current)

    accepted.sort(key=lambda s: (s.start, s.end))
    # Defensive: assert disjointness, because the writer depends on it.
    for earlier, later in zip(accepted, accepted[1:]):
        if earlier.end > later.start:  # pragma: no cover - guarded by the sweep
            raise AssertionError(
                f"resolver produced overlapping spans: {earlier} and {later}"
            )

    stats.output_spans += len(accepted)
    for span in accepted:
        stats.by_type[str(span.pii_type)] += 1
    return accepted

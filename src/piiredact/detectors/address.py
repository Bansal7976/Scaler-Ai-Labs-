"""Postal address detection.

An address has no reliable left edge, which makes it the hardest of the required
types. What it almost always *does* have is a reliable right edge -- a postal
code -- so this detector works backwards from there:

**S1 -- column header.** A table cell under a column headed ``Address`` is an
address in its entirety. Exact, and it is what redacts the eight directors'
residential addresses in table 70 cleanly.

**S2 -- label anchoring.** ``Registered Office: ...``, ``Corporate Office: ...``,
``having its registered office at ...``. The label gives the left edge for free;
the right edge comes from the postal code or the end of the field.

**S3 -- postal-code anchoring.** Find an Indian PIN (``411 004``/``411004``) or a
US ZIP, then walk *left* to the nearest structural boundary -- a label colon, an
``at``/``located at`` preposition, a semicolon, a sentence end, or the start of
the segment -- capped at :data:`MAX_ADDRESS_CHARS`. Sentence-end detection
deliberately ignores abbreviation periods (``S. no.``, ``Plot No.``) which
otherwise truncate Indian addresses in the middle.

The expanded candidate must still contain at least one address component word
(``Road``, ``Village``, ``Floor``, ``Society``, ``Taluka``, ...) or a house
number, which is what stops a bare ``Pune - 411 004`` cross-reference in prose
from being reported as a full address.
"""

from __future__ import annotations

import re
from typing import Iterable

from ..lexicons import (
    ADDRESS_COMPONENT_WORDS,
    ADDRESS_LABELS,
    INDIAN_STATES,
    KNOWN_LOCALITIES,
    norm,
)
from ..types import PIIType, Segment, Span
from .base import BaseDetector, DetectionContext, register

#: Indian PIN code: six digits, optionally split 3+3, normally after a dash.
PIN_RE = re.compile(r"(?<![\d/-])[1-9]\d{2}\s?\d{3}(?![\d/-])")
#: US ZIP / ZIP+4 preceded by a two-letter state abbreviation.
US_ZIP_RE = re.compile(r"(?<![\w-])[A-Z]{2}\s+\d{5}(?:-\d{4})?(?![\d-])")

_LABEL_ALT = "|".join(
    re.escape(label) for label in sorted(set(ADDRESS_LABELS), key=len, reverse=True)
)
LABEL_RE = re.compile(rf"(?:{_LABEL_ALT})\s*[:\-–]?\s*", re.IGNORECASE)

#: Structural left boundaries, searched for immediately left of the anchor.
_BOUNDARY_RE = re.compile(
    r"(?:"
    r":\s*"
    r"|;\s*"
    r"|\|\s*"
    r"|\b(?:located|situated|registered|situate)\s+at\s+"
    r"|\boffice\s+at\s+"
    r"|\bpremises\s+at\s+"
    r"|\bfacility\s+(?:located\s+)?at\s+"
    r"|\baddress\s+(?:is\s+)?"
    r"|\bat\s+"
    r")",
    re.IGNORECASE,
)
#: A real sentence end: a lowercase word, then a period, then a capital.
_SENTENCE_END_RE = re.compile(r"(?<=[a-z]{2})\.\s+(?=[A-Z“])")
#: Legal-entity suffixes, imported as a boundary rather than re-derived, so the
#: address and organisation detectors can never disagree about where a company
#: name ends.
from .organization import SUFFIX_RE as _ORG_SUFFIX_RE  # noqa: E402
#: Prepositions that turn a following company name into a landmark.
_LANDMARK_LEAD_RE = re.compile(
    r"\b(?:above|below|near|behind|opposite|beside|adjacent\s+to|next\s+to|"
    r"off|in\s+front\s+of|beneath|under|over|across\s+from)\s+"
    r"(?:[A-Z][A-Za-z&.'’\-]*\s+){0,4}$",
    re.IGNORECASE,
)

#: The furthest left of an anchor the detector will reach.
MAX_ADDRESS_CHARS = 260
#: Minimum length before a candidate is considered a full address.
MIN_ADDRESS_CHARS = 18
#: Minimum length for the locality-plus-postal-code form. A long address in this
#: document is often split across paragraphs, leaving "Pune - 411 001" alone in a
#: paragraph of its own; it is still enough to locate a party, and the guideline
#: annotates it, so the length floor is relaxed for exactly that shape.
MIN_LOCALITY_PIN_CHARS = 9
LOCALITY_PIN_RE = re.compile(
    r"^[A-Z][A-Za-z.’' ]{2,30}[\s,]*[–—-]\s*[1-9]\d{2}\s?\d{3}$"
)

_HOUSE_NUMBER_RE = re.compile(r"\b(?:\d+[/\-]?\d*[A-Za-z]?|[A-Z]-?\d+)\b")
_ADDRESS_COLUMN_HEADERS = ("address", "addresses", "registered address",
                           "residential address", "office address",
                           "name, address, telephone and e-mail address of the underwriters")
_LEADING_TRIM_RE = re.compile(
    r"^(?:at|the|its|our|their|his|her|is|was|located|situated|and|of|in|on)\s+",
    re.IGNORECASE,
)


def has_address_evidence(text: str) -> bool:
    """True when ``text`` contains at least one positive address signal."""
    tokens = [norm(t.strip("().,;:–-")) for t in text.split()]
    if any(token in ADDRESS_COMPONENT_WORDS for token in tokens):
        return True
    if any(token in KNOWN_LOCALITIES for token in tokens):
        if _HOUSE_NUMBER_RE.search(text) or "," in text:
            return True
    if any(norm(state) in norm(text) for state in INDIAN_STATES):
        if _HOUSE_NUMBER_RE.search(text):
            return True
    return False


@register
class AddressDetector(BaseDetector):
    name = "address.anchored"
    pii_types = (PIIType.ADDRESS,)

    def detect(self, segment: Segment, ctx: DetectionContext) -> Iterable[Span]:
        emitted: list[tuple[int, int]] = []

        for span in self._candidates(segment):
            if any(s <= span.start and span.end <= e for s, e in emitted):
                continue
            emitted = [(s, e) for s, e in emitted if not (span.start <= s and e <= span.end)]
            emitted.append((span.start, span.end))
            ctx.gazetteer.add(span.text, PIIType.ADDRESS, self.name)
            yield span

    def _candidates(self, segment: Segment) -> Iterable[Span]:
        yield from self._from_column_header(segment)
        yield from self._from_anchor(segment)

    # -- S1: table column header --------------------------------------------

    def _from_column_header(self, segment: Segment) -> Iterable[Span]:
        header = norm(segment.column_header or "")
        if not any(header == candidate for candidate in _ADDRESS_COLUMN_HEADERS):
            return
        stripped = segment.text.strip()
        if len(stripped) < MIN_ADDRESS_CHARS or not has_address_evidence(stripped):
            return
        start = segment.text.index(stripped)
        yield self.span(
            segment,
            start,
            start + len(stripped),
            PIIType.ADDRESS,
            confidence=1.0,
            strategy="column-header",
            column_header=segment.column_header,
        )

    # -- S2/S3: label and postal-code anchoring ------------------------------

    def _from_anchor(self, segment: Segment) -> Iterable[Span]:
        text = segment.text
        anchors: list[tuple[int, int, str]] = []
        for match in PIN_RE.finditer(text):
            # Require a locality or a dash immediately before, so that share
            # counts and rupee figures are not treated as PIN codes.
            lead = text[max(0, match.start() - 24) : match.start()]
            if not re.search(r"[–—,\-]\s*$", lead) and not any(
                norm(loc) in norm(lead) for loc in KNOWN_LOCALITIES
            ):
                continue
            anchors.append((match.start(), match.end(), "pin"))
        for match in US_ZIP_RE.finditer(text):
            anchors.append((match.start(), match.end(), "us-zip"))

        for anchor_start, anchor_end, kind in anchors:
            start = self._left_boundary(text, anchor_start)
            end = self._extend_tail(text, anchor_end)
            candidate = text[start:end]
            trimmed = _LEADING_TRIM_RE.sub("", candidate)
            start += len(candidate) - len(trimmed)
            candidate = text[start:end].strip(" ,;:|")
            if not candidate:
                continue
            start = text.index(candidate, max(0, start - 2))
            end = start + len(candidate)
            locality_pin = bool(LOCALITY_PIN_RE.match(candidate)) and any(
                norm(part.strip(" ,")) in KNOWN_LOCALITIES
                for part in re.split(r"[–—-]", candidate)[:1]
            )
            floor = MIN_LOCALITY_PIN_CHARS if locality_pin else MIN_ADDRESS_CHARS
            if len(candidate) < floor:
                continue
            if not locality_pin and not has_address_evidence(candidate):
                continue
            labelled = bool(LABEL_RE.search(text[max(0, start - 60) : start + 4]))
            yield self.span(
                segment,
                start,
                end,
                PIIType.ADDRESS,
                confidence=0.95 if labelled else 0.88,
                strategy="label-anchored" if labelled else "postal-code-anchored",
                anchor=kind,
            )

    @staticmethod
    def _left_boundary(text: str, anchor_start: int) -> int:
        window_start = max(0, anchor_start - MAX_ADDRESS_CHARS)
        window = text[window_start:anchor_start]
        best = window_start
        for match in _BOUNDARY_RE.finditer(window):
            best = max(best, window_start + match.end())
        for match in _SENTENCE_END_RE.finditer(window):
            best = max(best, window_start + match.end())
        # A label sets the boundary precisely, and beats the generic ones.
        for match in LABEL_RE.finditer(window):
            best = max(best, window_start + match.end())
        # A legal-entity suffix ends the company name that precedes an address in
        # every "who to contact" block of the prospectus. Without this the
        # address swallowed "ICICI Securities Limited" and the company name was
        # replaced as part of the address surrogate instead of as itself.
        for match in _ORG_SUFFIX_RE.finditer(window):
            # Exception: a company name used as a *landmark* is part of the
            # address ("above HDFC Limited Karve Road"). The giveaway is the
            # address preposition immediately before the name, so the suffix is
            # only a boundary when no such preposition precedes it.
            if _LANDMARK_LEAD_RE.search(window[: match.start()]):
                continue
            best = max(best, window_start + match.end())
        return best

    @staticmethod
    def _extend_tail(text: str, anchor_end: int) -> int:
        """Absorb the ``, Maharashtra, India`` tail that follows a PIN code.

        Each step takes the *longest* following run of one to three capitalised
        words that is a known state, locality or "India", and stops otherwise.
        Matching a fixed multi-word group instead was subtly wrong: in a table
        cell the tail is followed by a newline and another field, and
        ``\\s`` matched the newline, so ", India\\nTel" was read as one chunk,
        failed the state test, and the tail was dropped entirely.
        """
        cursor = anchor_end
        for _ in range(3):
            separator = re.match(r"[ \t]*[,;]?[ \t]*", text[cursor:])
            after = cursor + (separator.end() if separator else 0)
            words = re.match(
                r"(\(?[A-Z][A-Za-z.]*\)?)(?:([ \t]+\(?[A-Z][A-Za-z.]*\)?)"
                r"(?:([ \t]+\(?[A-Z][A-Za-z.]*\)?))?)?",
                text[after:],
            )
            if words is None:
                break
            advanced = False
            for group_count in (3, 2, 1):
                chunk = "".join(g for g in words.groups()[:group_count] if g)
                if not chunk:
                    continue
                key = norm(chunk.strip("() "))
                if key in INDIAN_STATES or key in KNOWN_LOCALITIES or key == "india":
                    cursor = after + len(chunk)
                    advanced = True
                    break
            if not advanced:
                break
        return cursor

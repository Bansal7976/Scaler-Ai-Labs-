"""Date-of-birth detection.

A prospectus is saturated with dates -- incorporation dates, board resolution
dates, fiscal period ends, bid open/close dates, acquisition dates. In the KSH
document there are 147 distinct date mentions and none of them is a date of
birth. Treating "any date" as a DOB would therefore destroy the document and
score near-zero precision.

So this detector only fires on a date that is *tied to a birth cue*: a
``Date of Birth``/``DOB``/``born on`` label to its left, or a parenthetical
``(b. 1953)`` form. That is a deliberate precision-over-recall choice; the cost
is that an undated, unlabelled birth date written in free prose is missed, which
is recorded as a known limitation in the README.

``--dob-mode=contextual`` widens this to any date within a configurable window
of a birth-related word (``birth``, ``born``, ``birthday``, ``age``,
``aged``), for callers who would rather over-redact.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Iterable

from ..lexicons import DOB_LABELS
from ..types import PIIType, Segment, Span
from .base import BaseDetector, DetectionContext, register

_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|"
    "November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)

#: The date shapes worth recognising, longest-first inside one alternation.
DATE_RE = re.compile(
    rf"""
    (?<![\w/])
    (?:
        (?:\d{{1,2}}(?:st|nd|rd|th)?\s+)?(?:{_MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}
      | \d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})\.?,?\s+\d{{4}}
      | (?:{_MONTHS})\.?\s+\d{{4}}
      | \d{{1,2}}[/.\-]\d{{1,2}}[/.\-]\d{{2,4}}
      | \d{{4}}[/.\-]\d{{1,2}}[/.\-]\d{{1,2}}
    )
    (?![\w/])
    """,
    re.VERBOSE | re.IGNORECASE,
)

_LABEL_ALT = "|".join(re.escape(label) for label in sorted(set(DOB_LABELS), key=len, reverse=True))
LABELLED_RE = re.compile(
    rf"(?:{_LABEL_ALT})\s*[:\-–]?\s*(?P<date>{DATE_RE.pattern})",
    re.VERBOSE | re.IGNORECASE,
)
BORN_RE = re.compile(
    rf"\b(?:born|b\.)\s*(?:on|in)?\s*[:\-–]?\s*(?P<date>{DATE_RE.pattern})",
    re.VERBOSE | re.IGNORECASE,
)
BIRTH_CUE_RE = re.compile(r"\b(?:birth|born|birthday|age[ds]?)\b", re.IGNORECASE)

#: Widest window, in characters, between a birth cue and a date in contextual mode.
CONTEXTUAL_WINDOW = 60

#: A DOB must be old enough to belong to an adult and recent enough to be a
#: living person. Used only as a sanity filter on four-digit years.
_MIN_YEAR = date.today().year - 120
_MAX_YEAR = date.today().year


def _plausible_birth_year(text: str) -> bool:
    years = [int(y) for y in re.findall(r"\b(\d{4})\b", text)]
    if not years:
        return True  # two-digit year form; nothing to check
    return any(_MIN_YEAR <= year <= _MAX_YEAR for year in years)


@register
class DateOfBirthDetector(BaseDetector):
    name = "dob.contextual"
    pii_types = (PIIType.DOB,)

    def __init__(self, mode: str = "labelled") -> None:
        if mode not in ("labelled", "contextual"):
            raise ValueError(f"unknown dob mode: {mode!r}")
        self.mode = mode

    def detect(self, segment: Segment, ctx: DetectionContext) -> Iterable[Span]:
        text = segment.text
        emitted: set[tuple[int, int]] = set()

        for pattern, source in ((LABELLED_RE, "label"), (BORN_RE, "born-cue")):
            for match in pattern.finditer(text):
                key = (match.start("date"), match.end("date"))
                if key in emitted or not _plausible_birth_year(match.group("date")):
                    continue
                emitted.add(key)
                yield self.span(
                    segment, *key, PIIType.DOB, confidence=1.0, source=source
                )

        if self.mode != "contextual":
            return

        cues = [m.start() for m in BIRTH_CUE_RE.finditer(text)]
        if not cues:
            return
        for match in DATE_RE.finditer(text):
            key = (match.start(), match.end())
            if key in emitted:
                continue
            if not _plausible_birth_year(match.group(0)):
                continue
            distance = min(abs(match.start() - cue) for cue in cues)
            if distance > CONTEXTUAL_WINDOW:
                continue
            emitted.add(key)
            yield self.span(
                segment,
                *key,
                PIIType.DOB,
                confidence=0.6,
                source="birth-context",
                cue_distance=distance,
            )

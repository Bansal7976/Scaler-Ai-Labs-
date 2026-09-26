"""Telephone number detection.

Phone numbers are where naive regex does the most damage in a document like a
prospectus: it is dense with eight-to-twelve digit runs that are *not* phone
numbers -- DINs, CINs, SEBI registration numbers, PIN codes, share counts and
rupee amounts. This detector therefore works from three narrow sources instead
of one broad pattern:

1. **Label-anchored** -- a digit run following ``Telephone:``/``Tel:``/
   ``Mobile:``/``Fax:``. Highest confidence, and the only source allowed to
   match a bare national number with no ``+`` prefix.
2. **Country-code-prefixed** -- a run beginning ``+91``/``+1``/etc. anywhere in
   the text.
3. **Trunk-prefixed** -- Indian STD form ``022-68052182``, ``020 25618211``.

Every candidate is then validated: digit count must be plausible, the match must
not sit inside a PIN code or a registration identifier, and -- when the optional
``phonenumbers`` dependency is installed -- the number must be *possible* for
its region. Numbers rejected by validation are reported in the audit log with
the reason, so tuning is observable rather than guesswork.
"""

from __future__ import annotations

import re
from typing import Iterable

from ..lexicons import PHONE_LABELS, REGISTRATION_LABELS
from ..types import PIIType, Segment, Span
from .base import BaseDetector, DetectionContext, register

try:  # optional dependency -- the detector degrades gracefully without it
    import phonenumbers

    _HAVE_PHONENUMBERS = True
except ImportError:  # pragma: no cover - exercised only on minimal installs
    phonenumbers = None  # type: ignore[assignment]
    _HAVE_PHONENUMBERS = False


_LABEL_ALT = "|".join(re.escape(label) for label in PHONE_LABELS)
#: ``Telephone: +91 22 30752929, +91 22 30752928 and +91 22 30752914``
LABELLED_RE = re.compile(
    rf"(?:{_LABEL_ALT})\s*[:\-–]?\s*(?P<body>[+\d][\d\s\-+().,]{{6,90}})",
    re.IGNORECASE,
)
#: One number inside a labelled run.
_NUMBER_IN_BODY_RE = re.compile(r"\+?\s?\d(?:[\d\s\-().]{5,18}\d)?")

COUNTRY_CODE_RE = re.compile(r"\+\s?\d{1,3}(?:[\s\-().]{0,3}\d){6,14}")
TRUNK_RE = re.compile(r"(?<![\d.])0\d{2,4}[\s\-]\d{6,8}(?![\d.])")

_DIGITS_RE = re.compile(r"\d")
#: Indian PIN code, written "411 004" or "411004", usually after a dash.
PIN_CODE_RE = re.compile(r"[–—\-,]\s*\d{3}\s?\d{3}\b")

_MIN_DIGITS = 8
_MAX_DIGITS = 15
_REGISTRATION_WINDOW = 40


def digits_of(text: str) -> str:
    return "".join(_DIGITS_RE.findall(text))


def _preceded_by_registration_label(text: str, start: int) -> str | None:
    """Return the offending label if a registration label precedes ``start``."""
    window = text[max(0, start - _REGISTRATION_WINDOW) : start]
    lowered = window.lower()
    for label in REGISTRATION_LABELS:
        if label.lower() in lowered:
            return label
    return None


def _inside_pin_code(text: str, start: int, end: int) -> bool:
    for match in PIN_CODE_RE.finditer(text):
        if match.start() < end and start < match.end():
            return True
    return False


def _is_possible_number(raw: str) -> bool:
    """Plausibility check, using ``phonenumbers`` when available."""
    digits = digits_of(raw)
    if not _MIN_DIGITS <= len(digits) <= _MAX_DIGITS:
        return False
    if len(set(digits)) <= 2:  # 0000000000, 1111111111 -- placeholder junk
        return False
    if not _HAVE_PHONENUMBERS:
        return True
    candidate = raw.strip()
    try:
        parsed = phonenumbers.parse(
            candidate, None if candidate.startswith("+") else "IN"
        )
    except phonenumbers.NumberParseException:
        return False
    return phonenumbers.is_possible_number(parsed)


@register
class PhoneDetector(BaseDetector):
    name = "phone.pattern"
    pii_types = (PIIType.PHONE,)

    def detect(self, segment: Segment, ctx: DetectionContext) -> Iterable[Span]:
        text = segment.text
        seen: set[tuple[int, int]] = set()

        for span in self._labelled(segment, text):
            if (span.start, span.end) not in seen:
                seen.add((span.start, span.end))
                yield span

        for pattern, source, confidence in (
            (COUNTRY_CODE_RE, "country-code", 0.97),
            (TRUNK_RE, "trunk-prefix", 0.90),
        ):
            for match in pattern.finditer(text):
                start, end = self._tighten(text, match.start(), match.end())
                if (start, end) in seen or end <= start:
                    continue
                if not self._accept(text, start, end, source):
                    continue
                seen.add((start, end))
                yield self.span(
                    segment,
                    start,
                    end,
                    PIIType.PHONE,
                    confidence=confidence,
                    source=source,
                    digits=len(digits_of(text[start:end])),
                )

    # -- internals -----------------------------------------------------------

    def _labelled(self, segment: Segment, text: str) -> Iterable[Span]:
        for match in LABELLED_RE.finditer(text):
            body_start = match.start("body")
            body = match.group("body")
            for number in _NUMBER_IN_BODY_RE.finditer(body):
                start = body_start + number.start()
                end = body_start + number.end()
                start, end = self._tighten(text, start, end)
                if end <= start:
                    continue
                if not self._accept(text, start, end, "label", skip_label_check=True):
                    continue
                yield self.span(
                    segment,
                    start,
                    end,
                    PIIType.PHONE,
                    confidence=1.0,
                    source="label",
                    label=match.group(0).split(":")[0].strip(),
                    digits=len(digits_of(text[start:end])),
                )

    @staticmethod
    def _tighten(text: str, start: int, end: int) -> tuple[int, int]:
        """Shrink the match to start and end on a digit."""
        while start < end and not text[start].isdigit() and text[start] != "+":
            start += 1
        while end > start and not text[end - 1].isdigit():
            end -= 1
        return start, end

    def _accept(
        self,
        text: str,
        start: int,
        end: int,
        source: str,
        *,
        skip_label_check: bool = False,
    ) -> bool:
        raw = text[start:end]
        if not skip_label_check:
            if _preceded_by_registration_label(text, start):
                return False
        if _inside_pin_code(text, start, end):
            return False
        # A digit immediately before/after means we clipped a longer number.
        if start > 0 and text[start - 1].isdigit():
            return False
        if end < len(text) and text[end].isdigit():
            return False
        # Currency and percentage contexts.
        before = text[max(0, start - 3) : start]
        if any(symbol in before for symbol in ("₹", "$", "%")):
            return False
        return _is_possible_number(raw)

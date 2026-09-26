"""US Social Security Number detection.

The KSH prospectus is an Indian filing and contains no SSNs, so this detector
contributes nothing to the document's own metrics. It is still a first-class
module for two reasons: the assignment names SSN in the minimum set, and the
same pipeline is meant to be pointed at US documents. Its correctness is
demonstrated against the synthetic corpus in ``tests/fixtures`` and reported
separately in the evaluation report.

Validation follows the SSA's published allocation rules, which remove the great
majority of false positives that a bare ``\\d{3}-\\d{2}-\\d{4}`` produces:

* area number (first group) may not be ``000``, ``666`` or ``900``-``999``;
* group number (second) may not be ``00``;
* serial number (third) may not be ``0000``.

Unseparated nine-digit runs are accepted **only** next to an explicit label
(``SSN``, ``Social Security Number``), because in a financial document a bare
nine-digit number is far more likely to be an amount or an identifier.
"""

from __future__ import annotations

import re
from typing import Iterable

from ..types import PIIType, Segment, Span
from .base import BaseDetector, DetectionContext, register

SEPARATED_RE = re.compile(r"(?<![\d-])(\d{3})[-– ](\d{2})[-– ](\d{4})(?![\d-])")
LABELLED_RE = re.compile(
    r"(?:SSN|S\.S\.N\.?|Social\s+Security(?:\s+(?:Number|No\.?|#))?)"
    # Filler between the label and the value: "SSN is 219456789",
    # "Social Security Number of 512-88-7431".
    r"(?:\s+(?:is|was|are|of|reads|shown\s+as|recorded\s+as))?"
    r"\s*[:#\-–]?\s*(?P<num>(?<!\d)\d{3}[-– ]?\d{2}[-– ]?\d{4}(?!\d))",
    re.IGNORECASE,
)

_INVALID_AREAS = frozenset({"000", "666"})


def is_valid_ssn(area: str, group: str, serial: str) -> bool:
    """Apply the SSA allocation rules to a split SSN."""
    if area in _INVALID_AREAS or area.startswith("9"):
        return False
    if group == "00" or serial == "0000":
        return False
    return True


@register
class SSNDetector(BaseDetector):
    name = "ssn.pattern"
    pii_types = (PIIType.SSN,)

    def detect(self, segment: Segment, ctx: DetectionContext) -> Iterable[Span]:
        text = segment.text
        emitted: set[tuple[int, int]] = set()

        for match in LABELLED_RE.finditer(text):
            digits = re.sub(r"\D", "", match.group("num"))
            if len(digits) != 9:
                continue
            if not is_valid_ssn(digits[:3], digits[3:5], digits[5:]):
                continue
            key = (match.start("num"), match.end("num"))
            emitted.add(key)
            yield self.span(
                segment,
                *key,
                PIIType.SSN,
                confidence=1.0,
                source="label",
            )

        for match in SEPARATED_RE.finditer(text):
            if (match.start(), match.end()) in emitted:
                continue
            if not is_valid_ssn(match.group(1), match.group(2), match.group(3)):
                continue
            # A separated group is only an SSN when separated by hyphens; the
            # space-separated form collides with Indian PIN codes and dates.
            if " " in match.group(0):
                continue
            yield self.span(
                segment,
                match.start(),
                match.end(),
                PIIType.SSN,
                confidence=0.95,
                source="separated",
            )

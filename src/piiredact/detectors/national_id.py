"""Government and regulatory identifier detection (India-focused).

Not in the assignment's minimum list, but included on a deliberate argument: a
redaction that removes a name and leaves behind a unique key pointing at that
name is not a redaction. In this corpus every director's name sits in a table
next to their DIN, and every intermediary's name sits next to its SEBI
registration number. Either is enough to re-identify the party in one lookup
against a public register, so they are redacted for consistency with the names
they accompany. The README states this choice explicitly.

Identifiers handled:

===================  =========================================================
DIN                  Director Identification Number -- 8 digits, person-linked
CIN                  Corporate Identity Number -- 21 chars, entity-linked
PAN                  Permanent Account Number -- 10 chars, person or entity
Aadhaar              12 digits, person-linked, Verhoeff checksum verified
GSTIN                15 chars, entity-linked, embeds the entity's PAN
SEBI registration    ``IN[A-Z]\\d{9}`` -- intermediary registration
ICAI firm reg.       audit firm registration, e.g. ``105215W``
Peer review no.      audit firm peer-review certificate, e.g. ``014680``
Membership number    professional membership, e.g. ``M-140388``
Passport / DL        label-anchored only (formats are too generic otherwise)
===================  =========================================================

Structural detection: a table cell under a column headed ``DIN`` is treated as a
DIN whatever its contents, which is how the eight directors' DINs in table 70 of
the prospectus are caught without a label in the cell itself.
"""

from __future__ import annotations

import re
from typing import Iterable, NamedTuple

from ..lexicons import norm
from ..types import PIIType, Segment, Span
from .base import BaseDetector, DetectionContext, register


class IdRule(NamedTuple):
    """One identifier format."""

    kind: str
    pattern: "re.Pattern[str]"
    #: When set, the match only counts if this label appears just before it.
    requires_label: bool
    confidence: float
    #: Optional extra validation on the matched text.
    validator: "str | None" = None


# -- Aadhaar: Verhoeff checksum ---------------------------------------------
# The Verhoeff algorithm over the dihedral group D5. Implemented here rather
# than pulled in as a dependency because it is twelve lines and lets the
# detector reject the many 12-digit runs in the financial tables.
_D5_MUL = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_D5_PERM = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


def verhoeff_ok(digits: str) -> bool:
    """True when ``digits`` (including its trailing check digit) is valid."""
    if not digits.isdigit():
        return False
    checksum = 0
    for index, char in enumerate(reversed(digits)):
        checksum = _D5_MUL[checksum][_D5_PERM[index % 8][int(char)]]
    return checksum == 0


def aadhaar_ok(raw: str) -> bool:
    digits = re.sub(r"\D", "", raw)
    if len(digits) != 12 or digits[0] in "01":
        return False
    if len(set(digits)) <= 2:
        return False
    return verhoeff_ok(digits)


_VALIDATORS = {"aadhaar": aadhaar_ok}

_RULES: tuple[IdRule, ...] = (
    IdRule(
        "cin",
        re.compile(r"(?<![A-Z0-9])[ULun][0-9]{5}[A-Za-z]{2}[0-9]{4}[A-Za-z]{3}[0-9]{6}(?![A-Z0-9])"),
        requires_label=False,
        confidence=1.0,
    ),
    IdRule(
        "gstin",
        re.compile(r"(?<![A-Z0-9])[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z](?![A-Z0-9])"),
        requires_label=False,
        confidence=1.0,
    ),
    IdRule(
        "sebi_registration",
        re.compile(r"(?<![A-Z0-9])IN[A-Z][0-9]{9}(?![A-Z0-9])"),
        requires_label=False,
        confidence=1.0,
    ),
    IdRule(
        "pan",
        re.compile(r"(?<![A-Z0-9])[A-Z]{3}[ABCFGHJLPTK][A-Z][0-9]{4}[A-Z](?![A-Z0-9])"),
        requires_label=False,
        confidence=0.9,
    ),
    IdRule(
        "aadhaar",
        re.compile(r"(?<![\d-])[2-9][0-9]{3}[ -]?[0-9]{4}[ -]?[0-9]{4}(?![\d-])"),
        requires_label=False,
        confidence=0.95,
        validator="aadhaar",
    ),
    IdRule(
        "din",
        re.compile(r"(?<![\d-])[0-9]{8}(?![\d-])"),
        requires_label=True,
        confidence=1.0,
    ),
    IdRule(
        "icai_firm_registration",
        re.compile(r"(?<![A-Z0-9])[0-9]{6}[A-Z](?![A-Z0-9])"),
        requires_label=True,
        confidence=0.95,
    ),
    IdRule(
        "peer_review_number",
        re.compile(r"(?<![A-Za-z0-9./-])[0-9]{6}(?![A-Za-z0-9./-])"),
        requires_label=True,
        confidence=0.95,
    ),
    IdRule(
        "membership_number",
        re.compile(r"(?<![A-Z0-9])[A-Z]-?[0-9]{5,7}(?![A-Z0-9])"),
        requires_label=True,
        confidence=0.9,
    ),
    IdRule(
        "passport",
        re.compile(r"(?<![A-Z0-9])[A-PR-WYa-prwy][0-9]{7}(?![A-Z0-9])"),
        requires_label=True,
        confidence=0.95,
    ),
    IdRule(
        "driving_licence",
        re.compile(r"(?<![A-Z0-9])[A-Z]{2}[ -]?[0-9]{2}[ -]?[0-9]{4}[0-9]{7}(?![A-Z0-9])"),
        requires_label=True,
        confidence=0.95,
    ),
    IdRule(
        "voter_id",
        re.compile(r"(?<![A-Z0-9])[A-Z]{3}[0-9]{7}(?![A-Z0-9])"),
        requires_label=True,
        confidence=0.9,
    ),
)

#: Which label text licenses each label-required rule.
_LABELS: dict[str, tuple[str, ...]] = {
    "din": ("DIN", "Director Identification Number", "Directors Identification Number"),
    "icai_firm_registration": (
        "Firm Registration Number",
        "Firm registration number",
        "FRN",
        "ICAI Firm Registration",
        "Registration Number",
    ),
    "membership_number": (
        "Membership Number",
        "Membership No",
        "registration number",
        "Registration Number",
        "M. No",
    ),
    "peer_review_number": (
        "Peer review number",
        "Peer Review Number",
        "Peer review certificate",
        "Peer Review Certificate",
        "Peer review no",
    ),
    "passport": ("Passport",),
    "driving_licence": ("Driving Licence", "Driving License", "DL No", "Licence Number"),
    "voter_id": ("Voter ID", "EPIC", "Election Card"),
}
_LABEL_WINDOW = 45

#: Column headers that make an entire table cell an identifier of a given kind.
_COLUMN_HEADER_KINDS: tuple[tuple[str, str, "re.Pattern[str]"], ...] = (
    ("din", "din", re.compile(r"^\s*[0-9]{8}\s*$")),
    ("director identification number", "din", re.compile(r"^\s*[0-9]{8}\s*$")),
    ("pan", "pan", re.compile(r"^\s*[A-Z]{5}[0-9]{4}[A-Z]\s*$")),
    ("permanent account number", "pan", re.compile(r"^\s*[A-Z]{5}[0-9]{4}[A-Z]\s*$")),
    ("cin", "cin", re.compile(r"^\s*\S{21}\s*$")),
    ("aadhaar", "aadhaar", re.compile(r"^\s*[0-9][0-9 -]{10,16}\s*$")),
)


@register
class NationalIdDetector(BaseDetector):
    name = "national_id.rules"
    pii_types = (PIIType.NATIONAL_ID,)

    def detect(self, segment: Segment, ctx: DetectionContext) -> Iterable[Span]:
        text = segment.text
        emitted: set[tuple[int, int]] = set()

        # 1. Structural: the column header tells us what the cell holds.
        for span in self._from_column_header(segment):
            emitted.add((span.start, span.end))
            ctx.gazetteer.add(span.text, PIIType.NATIONAL_ID, self.name)
            yield span

        # 2. Pattern rules.
        for rule in _RULES:
            for match in rule.pattern.finditer(text):
                key = (match.start(), match.end())
                if key in emitted:
                    continue
                raw = match.group(0)
                if rule.requires_label and not self._labelled(text, match.start(), rule.kind):
                    continue
                validator = _VALIDATORS.get(rule.validator or "")
                if validator and not validator(raw):
                    continue
                if rule.kind == "pan" and norm(raw) in ctx.gazetteer.entries:
                    pass  # already known; still emit so every mention is replaced
                emitted.add(key)
                ctx.gazetteer.add(raw, PIIType.NATIONAL_ID, self.name)
                yield self.span(
                    segment,
                    *key,
                    PIIType.NATIONAL_ID,
                    confidence=rule.confidence,
                    id_kind=rule.kind,
                )

    # -- internals -----------------------------------------------------------

    def _from_column_header(self, segment: Segment) -> Iterable[Span]:
        header = norm(segment.column_header or "")
        if not header:
            return
        stripped = segment.text.strip()
        if not stripped:
            return
        for header_key, kind, shape in _COLUMN_HEADER_KINDS:
            if header_key not in header:
                continue
            if not shape.match(segment.text):
                continue
            start = segment.text.index(stripped)
            yield self.span(
                segment,
                start,
                start + len(stripped),
                PIIType.NATIONAL_ID,
                confidence=1.0,
                id_kind=kind,
                source="column-header",
                column_header=segment.column_header,
            )
            return

    @staticmethod
    def _labelled(text: str, start: int, kind: str) -> bool:
        window = text[max(0, start - _LABEL_WINDOW) : start].lower()
        return any(label.lower() in window for label in _LABELS.get(kind, ()))

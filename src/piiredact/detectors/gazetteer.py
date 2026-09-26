"""Pass 2: the document-wide gazetteer sweep.

Pass 1 is tuned for precision, which means it finds each entity only where the
document makes it structurally obvious -- in a table cell under a ``Name``
header, after a ``Contact Person:`` label, next to a legal suffix. That leaves
most *mentions* unredacted: "Kushal Subbayya Hegde" is caught in table 70, but
the same person is also "Mr. Hegde" in the risk factors and "Kushal Hegde" in the
capital-structure table.

This detector closes that gap. It takes the entity list pass 1 built, expands it
with derived surface forms, compiles one alternation per PII type, and re-scans
every segment. Because it only looks for entities already *proved* to be in the
document, it raises recall a long way without the precision cost of a looser
first pass.

Three details make the difference in practice:

* **Whitespace-flexible matching.** The docx stores ``Chairman\\tand\\tExecutive
  Director``; names and company names have the same problem. Each entity's tokens
  are joined with ``\\s+``, so a name split across a tab or a line break matches.
* **Longest-first alternation.** ``Kushal Subbayya Hegde`` must win over
  ``Hegde``, otherwise the sweep fragments full names.
* **Derived forms carry lower confidence.** A bare surname or a brand acronym is
  a real mention but a weaker inference than the full name, so it is emitted at
  reduced confidence and is separable in the audit log.
"""

from __future__ import annotations

import re
from typing import Iterable

from ..lexicons import norm
from ..types import PIIType, Segment, Span
from .base import PASS_GAZETTEER, BaseDetector, DetectionContext, register
from .organization import derive_brand_aliases, is_public_or_role
from .person import derive_short_forms, looks_like_person_name

#: Types swept in pass 2. Addresses are excluded on purpose: they are long and
#: near-unique, so a second pass adds nothing but the risk of a partial match.
SWEPT_TYPES: tuple[PIIType, ...] = (
    PIIType.PERSON,
    PIIType.ORGANIZATION,
    PIIType.EMAIL,
    PIIType.PHONE,
    PIIType.NATIONAL_ID,
    PIIType.URL,
    PIIType.ADDRESS,
)

#: Confidence for a form that was observed verbatim in pass 1.
_OBSERVED_CONFIDENCE = 0.95
#: Confidence for a form derived by contraction (surname, brand acronym).
_DERIVED_CONFIDENCE = 0.72
#: Surface forms shorter than this are never swept -- too collision-prone.
_MIN_SWEEP_LEN = 3
#: A single all-caps token no longer than this is swept **case-sensitively**.
#: Case-insensitive matching on a four-letter initialism is a trap: "CARE" is a
#: real short form of CARE Ratings in this document, and matching it without
#: regard to case redacted 43 occurrences of the ordinary English word "care".
_CASE_SENSITIVE_MAX_LEN = 6


def _flexible_pattern(surface: str) -> str:
    """Regex for one surface form, tolerant of whitespace and punctuation runs."""
    tokens = [re.escape(token) for token in surface.split()]
    body = r"[\s ]+".join(tokens)
    # Curly and straight apostrophes are interchangeable in the source document.
    body = body.replace(r"\'", r"['‘’]").replace(r"\\u2019", r"['‘’]")
    left = r"(?<![\w@.])" if surface[0].isalnum() else r"(?<![\w@])"
    right = r"(?![\w@])" if surface[-1].isalnum() else ""
    return f"{left}(?:{body}){right}"


@register
class GazetteerSweepDetector(BaseDetector):
    name = "gazetteer.sweep"
    pii_types = SWEPT_TYPES
    pass_number = PASS_GAZETTEER

    def __init__(self) -> None:
        #: PII type -> list of (compiled alternation, confidences, case_sensitive)
        self._compiled: dict[
            PIIType, list[tuple[re.Pattern[str], dict[str, float], bool]]
        ] = {}

    @staticmethod
    def _needs_case_sensitivity(form: str) -> bool:
        """True for a short all-caps initialism, which must match case exactly."""
        return (
            len(form) <= _CASE_SENSITIVE_MAX_LEN
            and " " not in form
            and form.isupper()
            and form.isalpha()
        )

    # -- called once by the pipeline before the pass -------------------------

    def prepare(self, ctx: DetectionContext) -> None:
        """Expand the gazetteer with derived forms and compile the matchers."""
        self._expand(ctx)
        self._compiled.clear()
        for pii_type in SWEPT_TYPES:
            forms = self._forms_for(ctx, pii_type)
            if not forms:
                continue
            groups: list[tuple[re.Pattern[str], dict[str, float], bool]] = []
            for case_sensitive in (False, True):
                subset = {
                    form: confidence
                    for form, confidence in forms.items()
                    if self._needs_case_sensitivity(form) is case_sensitive
                }
                if not subset:
                    continue
                ordered = sorted(subset, key=lambda s: (-len(s), s.casefold()))
                pattern = re.compile(
                    "|".join(_flexible_pattern(form) for form in ordered),
                    0 if case_sensitive else re.IGNORECASE,
                )
                key_of = (lambda s: s) if case_sensitive else norm
                confidences = {key_of(form): subset[form] for form in subset}
                groups.append((pattern, confidences, case_sensitive))
            self._compiled[pii_type] = groups

    def _expand(self, ctx: DetectionContext) -> None:
        """Add contracted surface forms for people and organisations."""
        gazetteer = ctx.gazetteer
        for key, (surface, pii_type) in list(gazetteer.entries.items()):
            # Expand canonical entries only. Deriving from an alias produces
            # shorter and shorter fragments and, worse, re-roots the alias.
            if gazetteer.aliases.get(key, key) != key:
                continue
            if pii_type is PIIType.PERSON:
                for short in derive_short_forms(surface):
                    if not looks_like_person_name(short, allow_single_token=True):
                        continue
                    gazetteer.add(
                        short, PIIType.PERSON, f"{self.name}:derived", canonical=surface
                    )
            elif pii_type is PIIType.ORGANIZATION:
                for alias in derive_brand_aliases(surface):
                    if is_public_or_role(alias):
                        continue
                    gazetteer.add(
                        alias,
                        PIIType.ORGANIZATION,
                        f"{self.name}:derived",
                        canonical=surface,
                    )

    def _forms_for(self, ctx: DetectionContext, pii_type: PIIType) -> dict[str, float]:
        """Surface forms of one type, mapped to the confidence they earn."""
        out: dict[str, float] = {}
        for key, (surface, entry_type) in ctx.gazetteer.entries.items():
            if entry_type is not pii_type:
                continue
            surface = surface.strip()
            if len(surface) < _MIN_SWEEP_LEN:
                continue
            derived = any(
                source.endswith(":derived")
                for source in ctx.gazetteer.sources.get(key, ())
            )
            only_derived = derived and all(
                source.endswith(":derived") for source in ctx.gazetteer.sources.get(key, ())
            )
            out[surface] = _DERIVED_CONFIDENCE if only_derived else _OBSERVED_CONFIDENCE
        return out

    # -- the pass itself -----------------------------------------------------

    def detect(self, segment: Segment, ctx: DetectionContext) -> Iterable[Span]:
        for pii_type, groups in self._compiled.items():
            for pattern, confidences, case_sensitive in groups:
                for match in pattern.finditer(segment.text):
                    matched = match.group(0)
                    if not matched.strip():
                        continue
                    lookup = matched if case_sensitive else norm(matched)
                    confidence = confidences.get(lookup, _DERIVED_CONFIDENCE)
                    yield self.span(
                        segment,
                        match.start(),
                        match.end(),
                        pii_type,
                        confidence=confidence,
                        strategy="gazetteer",
                        case_sensitive=case_sensitive,
                        canonical=ctx.gazetteer.canonical_text(matched),
                    )

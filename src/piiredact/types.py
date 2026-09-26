"""Core value types shared by every stage of the pipeline.

The whole tool passes around exactly two things: :class:`Span` (a detected
stretch of characters in one text segment) and :class:`Segment` (a unit of
document text plus the address needed to write it back).  Keeping these
deliberately small is what lets detectors, the overlap resolver, the surrogate
engine and the docx writer stay independent of one another.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Mapping


class PIIType(enum.Enum):
    """The categories of PII this tool recognises.

    Adding a new category is a two-line change here plus one detector module;
    see ``README.md`` -> "Extending to a new PII type".
    """

    PERSON = "PERSON"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    ORGANIZATION = "ORGANIZATION"
    ADDRESS = "ADDRESS"
    SSN = "SSN"
    CREDIT_CARD = "CREDIT_CARD"
    DOB = "DOB"
    IP_ADDRESS = "IP_ADDRESS"
    # National / regulatory identifiers.  Not in the assignment's minimum set,
    # but they are direct identifiers that sit next to the names in this corpus,
    # so leaving them in place would undo the rest of the redaction.
    NATIONAL_ID = "NATIONAL_ID"
    URL = "URL"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


#: Resolution priority when two spans overlap: lower number wins.
#: Rationale: a structurally unambiguous match (an email, a card number) should
#: never be broken up by a fuzzier one (a name, an organisation, an address).
TYPE_PRIORITY: Mapping[PIIType, int] = {
    PIIType.EMAIL: 10,
    PIIType.URL: 15,
    PIIType.CREDIT_CARD: 20,
    PIIType.SSN: 25,
    PIIType.IP_ADDRESS: 30,
    PIIType.NATIONAL_ID: 35,
    PIIType.PHONE: 40,
    PIIType.DOB: 50,
    PIIType.ADDRESS: 60,
    PIIType.ORGANIZATION: 70,
    PIIType.PERSON: 80,
}


@dataclass(frozen=True, slots=True)
class Span:
    """One detected PII mention inside a single :class:`Segment`.

    ``start``/``end`` are Python string offsets into ``Segment.text`` and follow
    the usual half-open convention.
    """

    start: int
    end: int
    text: str
    pii_type: PIIType
    detector: str
    confidence: float = 1.0
    #: Free-form detector notes; surfaced in the audit log for review.
    evidence: dict[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        if self.start < 0 or self.end <= self.start:
            raise ValueError(f"invalid span bounds: [{self.start}, {self.end})")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence out of range: {self.confidence}")

    @property
    def length(self) -> int:
        return self.end - self.start

    @property
    def priority(self) -> int:
        return TYPE_PRIORITY[self.pii_type]

    def overlaps(self, other: "Span") -> bool:
        return self.start < other.end and other.start < self.end

    def contains(self, other: "Span") -> bool:
        return self.start <= other.start and other.end <= self.end

    def shifted(self, delta: int) -> "Span":
        return Span(
            self.start + delta,
            self.end + delta,
            self.text,
            self.pii_type,
            self.detector,
            self.confidence,
            dict(self.evidence),
        )


@dataclass(slots=True)
class Segment:
    """A contiguous run of document text that is detected and rewritten as one.

    ``location`` is a human-readable breadcrumb ("table 70, row 3, col 2") used
    in the audit log; ``key`` is the opaque handle the docx writer uses to find
    the same place again.

    The table fields matter more than they look. A cell sitting under a column
    headed "DIN" *is* a Director Identification Number, and a cell under
    "Address" *is* a postal address, whatever its text looks like in isolation.
    Carrying the header down to the detectors converts the document's own layout
    into a high-precision signal, and is the single biggest reason the structured
    tables in this prospectus are redacted cleanly.
    """

    key: str
    text: str
    location: str
    kind: str  # "paragraph" | "table-cell"
    #: Section heading in force at this point in the document, when known.
    section: str | None = None
    #: Nearest preceding heading level-3 or the paragraph style name.
    style: str | None = None
    #: Table coordinates, present only when ``kind == "table-cell"``.
    table_index: int | None = None
    row_index: int | None = None
    col_index: int | None = None
    #: Text of the header cell above this cell, normalised to a single line.
    column_header: str | None = None
    #: Text of the first cell in this cell's row (often the entity being
    #: described), used as a row label.
    row_header: str | None = None

    def __len__(self) -> int:
        return len(self.text)

    @property
    def is_table_cell(self) -> bool:
        return self.kind == "table-cell"

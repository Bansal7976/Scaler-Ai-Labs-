"""Email address detection.

Email addresses are the one PII type with a genuinely reliable surface form, so
this detector is plain regex and runs at full confidence.

It does one extra job: an address such as ``tushar.gavankar@hdfcbank.com``
*names a person*. The local part is split into name tokens and, when it looks
like a human name rather than a role mailbox, the reconstructed name is seeded
into the gazetteer as a PERSON candidate. Pass 2 then finds "Tushar Gavankar"
written out in prose elsewhere in the document, which the name detectors on
their own miss. Role mailboxes (``ipo@``, ``customercare@``, ``cs.connect@``)
are excluded by :data:`ROLE_LOCAL_PARTS`.
"""

from __future__ import annotations

import re
from typing import Iterable

from ..lexicons import norm
from ..types import PIIType, Segment, Span
from .base import BaseDetector, DetectionContext, register

# Deliberately slightly stricter than RFC 5322: the TLD must be alphabetic and
# at least two characters, which removes matches such as "v1.2@3" from tables of
# financial ratios without losing any real address.
EMAIL_RE = re.compile(
    r"""
    (?<![\w.+-])
    (?P<local>[A-Za-z0-9](?:[A-Za-z0-9._%+\-]{0,62}[A-Za-z0-9])?)
    @
    (?P<domain>(?:[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,24})
    (?![\w\-])
    """,
    re.VERBOSE,
)

#: Local parts that denote a shared/functional mailbox, not an individual.
ROLE_LOCAL_PARTS = frozenset(
    {
        "info", "contact", "support", "help", "helpdesk", "admin", "office",
        "sales", "enquiry", "enquiries", "inquiry", "queries", "query",
        "ipo", "ipos", "investor", "investors", "investorrelations", "ir",
        "customercare", "customerservice", "customer", "care", "service",
        "compliance", "secretarial", "cs", "grievance", "grievances",
        "complaints", "redressal", "legal", "pro", "press", "media",
        "hr", "careers", "jobs", "recruitment", "noreply", "no-reply",
        "webmaster", "postmaster", "mail", "email", "general", "corp",
        "corporate", "connect", "reachus", "escalation", "escalations",
    }
)

#: Local-part tokens that are structural, not part of a person's name.
_NOISE_TOKENS = frozenset({"mb", "in", "co", "ltd", "india", "ipo", "cmg", "rm6", "pro"})

_SPLIT_RE = re.compile(r"[._\-+]+")
_TRAILING_DIGITS_RE = re.compile(r"\d+$")


def _person_name_from_local_part(local: str) -> str | None:
    """Reconstruct "Firstname Lastname" from an email local part, or None.

    Only returns a name when the local part splits into two or three alphabetic
    tokens of plausible length -- the shape of ``first.last`` and
    ``first.middle.last`` corporate addresses.
    """
    if norm(local) in ROLE_LOCAL_PARTS:
        return None
    raw_tokens = [t for t in _SPLIT_RE.split(local) if t]
    tokens: list[str] = []
    for token in raw_tokens:
        token = _TRAILING_DIGITS_RE.sub("", token)  # "pravin.teli2" -> "teli"
        if not token or not token.isalpha():
            return None
        if norm(token) in _NOISE_TOKENS:
            continue
        if norm(token) in ROLE_LOCAL_PARTS:
            return None
        tokens.append(token)
    if not 2 <= len(tokens) <= 3:
        return None
    if any(len(t) < 2 for t in tokens):
        return None
    if all(len(t) <= 2 for t in tokens):
        return None
    return " ".join(t.capitalize() for t in tokens)


@register
class EmailDetector(BaseDetector):
    name = "email.regex"
    pii_types = (PIIType.EMAIL,)

    def detect(self, segment: Segment, ctx: DetectionContext) -> Iterable[Span]:
        for match in EMAIL_RE.finditer(segment.text):
            address = match.group(0)
            ctx.gazetteer.add(address, PIIType.EMAIL, self.name)
            hint = _person_name_from_local_part(match.group("local"))
            if hint:
                # Seed only; the PERSON span itself is emitted by the gazetteer
                # sweep in pass 2, which keeps provenance honest in the audit log.
                ctx.gazetteer.add(hint, PIIType.PERSON, self.name)
            yield self.span(
                segment,
                match.start(),
                match.end(),
                PIIType.EMAIL,
                confidence=1.0,
                domain=match.group("domain"),
                name_hint=hint,
            )

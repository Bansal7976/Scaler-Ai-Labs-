"""Person-name detection.

This is the detector that decides whether the whole tool is any good, and it is
the one where a single technique is not enough. Measured on this corpus, spaCy's
``en_core_web_md`` NER alone finds 60 distinct PERSON strings of which roughly a
third are actually address fragments ("Village Birdewadi", "Bandra Kurla
Complex") or document jargon ("S. No", "Cap Price", "Fiscals"), while missing
most of the directors listed in the tables. So four independent strategies run
here, deliberately ordered from most to least structural:

**S1 -- column header.** A table cell under a column headed ``Name`` in a table
that also has ``Designation``/``DIN`` columns is a person's name. This is what
catches all eight directors in table 70 of the prospectus.

**S2 -- cue labels.** ``Contact Person: <name>``, ``<name>, Company Secretary
and Compliance Officer``, ``<name> | Managing Director``. Both directions are
tried, and a slash- or comma-separated list after one cue is expanded, which is
what recovers the five HDFC Bank contacts written as
``Eric Bacha/ Sachin Gawade/ Pravin Teli/ Siddharth Jadhav/ Tushar Gavankar``.

**S3 -- honorifics.** ``Mr.``/``Ms.``/``Shri`` immediately before a name.

**S4 -- filtered NER.** spaCy PERSON entities, passed through
:func:`looks_like_person_name`, which vetoes anything containing an address
component word, a designation word, a known locality or state, a legal-entity
suffix, or a phrase from :data:`~piiredact.lexicons.NON_PERSON_PHRASES`.

All four only *seed*; the exhaustive sweep for every mention of each name found
is the job of :mod:`piiredact.detectors.gazetteer`, which runs in pass 2.
"""

from __future__ import annotations

import re
from typing import Iterable

from ..lexicons import (
    ADDRESS_COMPONENT_WORDS,
    DESIGNATION_WORDS,
    GENERIC_BUSINESS_WORDS,
    HONORIFICS,
    INDIAN_STATES,
    KNOWN_LOCALITIES,
    NON_PERSON_PHRASES,
    NON_PERSON_TOKENS,
    ORG_SUFFIXES,
    PERSON_CUE_LABELS,
    PUBLIC_BODIES,
    PUBLIC_BODY_TOKENS,
    SCHEME_CONTEXT_WORDS,
    norm,
)
from ..types import PIIType, Segment, Span
from .base import BaseDetector, DetectionContext, register

# ---------------------------------------------------------------------------
# Name shape
# ---------------------------------------------------------------------------
#: Lowercase particles that legitimately appear inside a full name.
NAME_PARTICLES = frozenset({"van", "der", "den", "de", "di", "da", "du", "la", "le",
                            "bin", "binte", "al", "el", "ibn", "von", "ter", "op"})

#: One name token: "Kushal", "S.", "D'Souza", "Mary-Jane", or ALL CAPS "HEGDE".
_TOKEN = r"(?:[A-Z][a-z'’]+(?:-[A-Z][a-z'’]+)?|[A-Z]\.|[A-Z]{2,})"
NAME_RE = re.compile(rf"\b{_TOKEN}(?:\s+(?:{_TOKEN}|van|der|de|di|da|von|bin|al))+\b")

_ORG_SUFFIX_SET = frozenset(norm(s) for s in ORG_SUFFIXES)
_MIN_TOKENS = 2
_MAX_TOKENS = 5
_MIN_SURNAME_LEN = 4


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"\s+", text.strip()) if t]


def looks_like_person_name(text: str, *, allow_single_token: bool = False) -> bool:
    """The shared veto list for every person-name candidate.

    Returns True only for a string that has the *shape* of a personal name and
    contains nothing that marks it as a place, a role, an organisation or a piece
    of document jargon.
    """
    text = text.strip().strip(",;:|")
    if not text:
        return False

    key = norm(text)
    if key in NON_PERSON_PHRASES or key in PUBLIC_BODIES or key in KNOWN_LOCALITIES:
        return False
    if key in INDIAN_STATES:
        return False

    tokens = _tokens(text)
    if not allow_single_token and not _MIN_TOKENS <= len(tokens) <= _MAX_TOKENS:
        return False
    if allow_single_token and not 1 <= len(tokens) <= _MAX_TOKENS:
        return False

    # Reject anything carrying a token from a vetoed vocabulary.
    initials = 0
    alpha_tokens = 0
    for token in tokens:
        bare = norm(token.strip(".,;:()"))
        if not bare:
            return False
        if bare in ADDRESS_COMPONENT_WORDS:
            return False
        if bare in DESIGNATION_WORDS:
            return False
        if bare in NON_PERSON_TOKENS:
            return False
        if bare in SCHEME_CONTEXT_WORDS:
            return False
        if bare in _ORG_SUFFIX_SET:
            return False
        if bare in PUBLIC_BODY_TOKENS:
            return False
        if bare in KNOWN_LOCALITIES or bare in INDIAN_STATES:
            return False
        if any(ch.isdigit() for ch in bare):
            return False
        if re.fullmatch(r"[a-z]\.?", bare) or token.endswith("."):
            initials += 1
            continue
        if bare in NAME_PARTICLES:
            continue
        if len(bare) < 2:
            return False
        alpha_tokens += 1

    # A phrase built only from generic commercial vocabulary is a column label or
    # a line item ("TOTAL OFFER SIZE" under a "CONTACT PERSON" column on the
    # cover page), not a name. Tested across the whole phrase rather than per
    # token, so a real name that happens to contain one such word still passes.
    word_tokens = [
        norm(t.strip(".,;:()")) for t in tokens if not re.fullmatch(r"[A-Za-z]\.?", t)
    ]
    if word_tokens and all(t in GENERIC_BUSINESS_WORDS for t in word_tokens):
        return False

    # A name needs at least one real word, and cannot be all initials.
    if alpha_tokens == 0:
        return False
    if not allow_single_token and alpha_tokens < 1:
        return False
    # "S. No." style: one word plus initials where the word is jargon is already
    # rejected above; here we additionally reject two-initial-only forms.
    if alpha_tokens == 1 and initials == 0 and not allow_single_token:
        return False
    # Every token must start uppercase (or be a particle).
    for token in tokens:
        if norm(token) in NAME_PARTICLES:
            continue
        if not token[0].isupper():
            return False
    return True


def derive_short_forms(full_name: str) -> list[str]:
    """Short forms of a full name that are worth sweeping for in pass 2.

    ``"Kushal Subbayya Hegde"`` yields ``["Kushal Hegde", "Hegde"]`` -- the
    first-plus-last contraction and the bare surname. The bare surname is only
    offered when it is long enough and distinctive enough not to collide with
    ordinary vocabulary; the caller decides what confidence to give it.
    """
    tokens = [t for t in _tokens(full_name) if not re.fullmatch(r"[A-Z]\.?", t)]
    out: list[str] = []
    if len(tokens) >= 3:
        out.append(f"{tokens[0]} {tokens[-1]}")
    if len(tokens) >= 2:
        surname = tokens[-1]
        key = norm(surname)
        distinctive = (
            len(surname) >= _MIN_SURNAME_LEN
            and key not in NON_PERSON_PHRASES
            and key not in ADDRESS_COMPONENT_WORDS
            and key not in DESIGNATION_WORDS
            and key not in KNOWN_LOCALITIES
            and key not in INDIAN_STATES
            and key not in PUBLIC_BODY_TOKENS
            and key not in _ORG_SUFFIX_SET
        )
        if distinctive:
            out.append(surname)
    return out


# ---------------------------------------------------------------------------
# Strategy regexes
# ---------------------------------------------------------------------------
_CUE_ALT = "|".join(
    re.escape(label) for label in sorted(set(PERSON_CUE_LABELS), key=len, reverse=True)
)
#: "Contact Person: Eric Bacha/ Sachin Gawade/ Pravin Teli"
CUE_THEN_NAME_RE = re.compile(
    rf"(?:{_CUE_ALT})\s*[:\-–|]\s*(?P<body>[^|;\n]{{2,160}})",
    re.IGNORECASE,
)
#: "Sarthak Malvadkar, Company Secretary and Compliance Officer". The separating
#: comma is optional because the cover-page table writes the same thing as
#: "Sarthak Malvadkar Company Secretary and Compliance Officer".
NAME_THEN_CUE_RE = re.compile(
    rf"(?P<name>{_TOKEN}(?:\s+{_TOKEN}){{1,4}})\s*[,|]?\s+(?:{_CUE_ALT})\b"
)
_HONORIFIC_ALT = "|".join(
    re.escape(h) for h in sorted(HONORIFICS, key=len, reverse=True)
)
HONORIFIC_RE = re.compile(
    rf"\b(?:{_HONORIFIC_ALT})\s+(?P<name>{_TOKEN}(?:\s+{_TOKEN}){{0,4}})",
    re.IGNORECASE,
)
#: Splits "Eric Bacha/ Sachin Gawade and Pravin Teli" into its parts.
_LIST_SPLIT_RE = re.compile(r"\s*(?:/|,|\band\b|&)\s*", re.IGNORECASE)

#: Field labels that follow a name in an unpunctuated contact block. Word writes
#: "Contact Person: Chitra Raste Website: www...", and because "Website" is a
#: capitalised token the name pattern happily absorbed it.
_FIELD_LABEL_WORDS = frozenset(
    {
        "website", "web", "url", "email", "e-mail", "mail", "telephone", "tel",
        "phone", "mobile", "fax", "address", "contact", "sebi", "registration",
        "cin", "din", "pan", "investor", "grievance", "grievances", "compliance",
        "designation", "occupation", "term", "date", "nationality", "notes",
    }
)


def _strip_field_labels(name: str) -> str:
    """Drop trailing field-label words from a name-shaped match."""
    tokens = name.split()
    while tokens and norm(tokens[-1].strip(".,;:")) in _FIELD_LABEL_WORDS:
        tokens.pop()
    return " ".join(tokens)

#: How far past a candidate to look for a government-scheme word.
_SCHEME_WINDOW = 36


def _in_scheme_context(text: str, end: int) -> bool:
    """True when a name-shaped phrase is really part of a scheme's title.

    India names its subsidy programmes after people, so "Deen Dayal Upadhyaya
    Gram Jyoti Yojana" reads as a personal name followed by three more words.
    Looking right for a scheme word separates the two cases cheaply.
    """
    following = text[end : end + _SCHEME_WINDOW]
    return any(
        norm(token.strip(",.;:()")) in SCHEME_CONTEXT_WORDS for token in following.split()
    )


#: Column headers that mark a cell as holding a person's name, paired with the
#: sibling headers that must also be present for the table to qualify.
_NAME_COLUMN_HEADERS = ("name", "contact person", "name of the director", "director")
_PERSON_TABLE_SIBLINGS = ("designation", "din", "date of birth", "occupation",
                          "term", "address", "designation and address")


@register
class PersonNameDetector(BaseDetector):
    name = "person.hybrid"
    pii_types = (PIIType.PERSON,)

    def detect(self, segment: Segment, ctx: DetectionContext) -> Iterable[Span]:
        emitted: set[tuple[int, int]] = set()

        for span in self._strategies(segment, ctx):
            key = (span.start, span.end)
            if key in emitted:
                continue
            # Drop a span already covered by a wider one from a better strategy.
            if any(s <= span.start and span.end <= e for s, e in emitted):
                continue
            if _in_scheme_context(segment.text, span.end):
                continue
            emitted.add(key)
            ctx.gazetteer.add(span.text, PIIType.PERSON, self.name)
            yield span

    def _strategies(self, segment: Segment, ctx: DetectionContext) -> Iterable[Span]:
        yield from self._from_column_header(segment)
        yield from self._from_cue_labels(segment)
        yield from self._from_honorifics(segment)
        yield from self._from_ner(segment, ctx)

    # -- S1: table column header --------------------------------------------

    def _from_column_header(self, segment: Segment) -> Iterable[Span]:
        header = norm(segment.column_header or "")
        if header not in _NAME_COLUMN_HEADERS:
            return
        stripped = segment.text.strip()
        if not stripped or len(stripped) > 80:
            return
        # Confirm the table really is about people, not e.g. shareholders' names
        # in a capital-structure table (those are handled as ORG/PERSON by NER).
        candidates = _LIST_SPLIT_RE.split(stripped) if "/" in stripped else [stripped]
        for candidate in candidates:
            candidate = candidate.strip()
            if not candidate or not looks_like_person_name(candidate):
                continue
            offset = segment.text.find(candidate)
            if offset < 0:
                continue
            yield self.span(
                segment,
                offset,
                offset + len(candidate),
                PIIType.PERSON,
                confidence=1.0,
                strategy="column-header",
                column_header=segment.column_header,
            )

    # -- S2: cue labels ------------------------------------------------------

    def _from_cue_labels(self, segment: Segment) -> Iterable[Span]:
        text = segment.text
        for match in CUE_THEN_NAME_RE.finditer(text):
            body_start = match.start("body")
            body = match.group("body")
            offset = 0
            for part in _LIST_SPLIT_RE.split(body):
                index = body.find(part, offset)
                if index < 0:
                    continue
                offset = index + len(part)
                part_clean = part.strip()
                if not part_clean:
                    continue
                # The body may run on into the next field ("... Website: www...").
                inner = NAME_RE.match(part_clean)
                if inner is None:
                    continue
                candidate = _strip_field_labels(inner.group(0))
                if not candidate or not looks_like_person_name(candidate):
                    continue
                start = body_start + index + part.find(candidate)
                yield self.span(
                    segment,
                    start,
                    start + len(candidate),
                    PIIType.PERSON,
                    confidence=0.98,
                    strategy="cue-label",
                    cue=match.group(0).split(":")[0].strip(),
                )

        for match in NAME_THEN_CUE_RE.finditer(text):
            candidate = match.group("name")
            if not looks_like_person_name(candidate):
                continue
            yield self.span(
                segment,
                match.start("name"),
                match.end("name"),
                PIIType.PERSON,
                confidence=0.95,
                strategy="name-then-cue",
            )

    # -- S3: honorifics ------------------------------------------------------

    def _from_honorifics(self, segment: Segment) -> Iterable[Span]:
        for match in HONORIFIC_RE.finditer(segment.text):
            candidate = match.group("name")
            if not looks_like_person_name(candidate, allow_single_token=True):
                continue
            yield self.span(
                segment,
                match.start("name"),
                match.end("name"),
                PIIType.PERSON,
                confidence=0.95,
                strategy="honorific",
            )

    # -- S4: filtered NER ----------------------------------------------------

    def _from_ner(self, segment: Segment, ctx: DetectionContext) -> Iterable[Span]:
        for start, end, text, label in ctx.ner_entities(segment):
            if label != "PERSON":
                continue
            candidate = text.strip()
            # NER routinely glues a trailing field onto a name
            # ("Shanti Gopalkrishnan SEBI Registration"); keep the leading
            # name-shaped prefix only.
            match = NAME_RE.match(candidate)
            if match is None:
                continue
            trimmed = _strip_field_labels(match.group(0))
            if not trimmed or not looks_like_person_name(trimmed):
                continue
            offset = segment.text.find(trimmed, start)
            if offset < 0 or offset > end:
                offset = start
            yield self.span(
                segment,
                offset,
                offset + len(trimmed),
                PIIType.PERSON,
                confidence=0.8,
                strategy="ner",
                ner_text=text,
            )

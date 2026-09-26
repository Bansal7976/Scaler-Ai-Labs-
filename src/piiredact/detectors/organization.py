"""Organisation-name detection.

spaCy's ORG labels are close to unusable on a prospectus: on this corpus they
return 326 distinct strings whose top entries are "Offer", "this Red Herring
Prospectus", "Fiscals 2025" and "the Promoter Selling Shareholders". So NER is
not the primary source here. Instead the detector anchors on the thing that
actually marks a company in a legal document -- its **legal-entity suffix** --
and grows the name leftwards from there.

Three strategies:

**S1 -- suffix anchoring.** Find ``Private Limited``/``Limited``/``LLP``/
``Family Trust``/``Corporation``/... and expand left across name-shaped tokens
(capitalised words, ALL-CAPS acronyms, ``&``, Roman numerals, parenthesised
qualifiers such as ``(India)``) until a boundary. ``of <Place>`` tails are then
absorbed on the right so ``Export-Import Bank of India`` is not clipped to
``Export-Import Bank``.

**S2 -- defined aliases.** A prospectus introduces every party once as
``CARE Analytics and Advisory Private Limited ("CareEdge Research")``. The quoted
alias is registered as another surface form of the same entity, so that later
bare uses of ``CareEdge Research`` are redacted to the *same* surrogate.

**S3 -- brand-token derivation.** ``KSH International Limited`` also appears as
``KSH International`` and as the bare trademark ``KSH``. Leading distinctive
tokens are registered as aliases at reduced confidence.

The precision policy lives in two lexicons rather than in this code:
:data:`~piiredact.lexicons.PUBLIC_BODIES` (regulators, exchanges, government --
never redacted) and :data:`~piiredact.lexicons.ORG_ROLE_PHRASES` (contractual
roles such as "Escrow Collection Bank", which are not entity names).
"""

from __future__ import annotations

import re
from typing import Iterable

from ..lexicons import (
    DESIGNATION_WORDS,
    GENERIC_BUSINESS_WORDS,
    NON_PERSON_PHRASES,
    ORG_ROLE_PHRASES,
    ORG_SUFFIXES,
    PUBLIC_BODIES,
    PUBLIC_BODY_TOKENS,
    WEAK_ORG_SUFFIXES,
    norm,
)
from ..types import PIIType, Segment, Span
from .base import BaseDetector, DetectionContext, register

_SUFFIX_ALT = "|".join(
    re.escape(s) for s in sorted(ORG_SUFFIXES, key=len, reverse=True)
)
SUFFIX_RE = re.compile(rf"\b(?:{_SUFFIX_ALT})(?![A-Za-z])", re.IGNORECASE)

#: ``<Name> ("<Alias>")`` -- the defined-term form used throughout a prospectus.
#: The ``name`` group is restricted to a compact proper-noun phrase (capitalised
#: tokens plus connectives) rather than "anything up to the bracket". With the
#: looser form, ``...are proposed to be listed on the BSE Limited ("BSE")``
#: registered the whole clause as an organisation and, worse, registered ``BSE``
#: as one of its aliases, defeating the public-body allowlist.
_ALIAS_NAME_TOKEN = r"(?:[A-Z][A-Za-z0-9&.'’\-]*|\([A-Za-z]{2,}\)|&)"
DEFINED_ALIAS_RE = re.compile(
    rf"(?P<name>{_ALIAS_NAME_TOKEN}"
    rf"(?:\s+(?:{_ALIAS_NAME_TOKEN}|and|of|the|for|de)){{0,7}})"
    r"\s*\(\s*(?:formerly[^()]{0,40}?)?[“\"'‘]\s*"
    r"(?P<alias>[^”\"'’\n]{2,60}?)\s*[”\"'’]\s*\)"
)

#: ``of India``-style tails absorbed to the right of a suffix.
OF_TAIL_RE = re.compile(r"\A\s+of\s+(?:the\s+)?[A-Z][A-Za-z.’']*(?:\s+[A-Z][A-Za-z.’']*)*")

_TOKEN_RE = re.compile(
    r"""
    \(?[A-Z][A-Za-z&.'’\-]*\)?   # Capitalised / ALL-CAPS / (India) / Export-Import
  | \(?[IVXLC]{1,6}\)?                # Roman numeral qualifier: VI, IX A
  | &
    """,
    re.VERBOSE,
)

#: Lowercase words that may sit *inside* a company name.
_INNER_CONNECTIVES = frozenset({"and", "of", "the", "for", "de", "van", "der", "&"})

#: Capitalised words that are never the first word of a company name.
_LEADING_STOP_WORDS = frozenset(
    {
        "formerly", "erstwhile", "namely", "viz", "including", "includes",
        "and", "or", "both", "either", "neither", "our", "their", "its",
        "his", "her", "this", "that", "these", "those", "such", "each",
        "any", "all", "other", "another", "certain", "said", "above",
        "below", "respectively", "together", "along", "with", "at", "by",
        "to", "from", "for", "in", "on", "as", "is", "was", "are", "were",
        "namely,", "i.e.", "e.g.", "vide", "pursuant", "under", "between",
        "among", "against", "into", "upon", "per", "than", "then", "also",
        "while", "whereas", "however", "further", "moreover", "accordingly",
        "namely:", "viz.", "mr", "mr.", "ms", "ms.", "mrs", "mrs.", "dr", "dr.",
        "shri", "smt", "smt.",
    }
)

#: Words that, appearing immediately *after* a weak suffix, prove the suffix was
#: part of a longer phrase rather than the end of a company name.
#: "Sarthak Malvadkar Company Secretary" and "the Companies Act" are the two
#: shapes this catches in the prospectus.
_RIGHT_VETO_WORDS = frozenset(
    {
        "secretary", "secretaries", "secretarial", "act", "acts", "law", "laws",
        "rules", "regulations", "name", "names", "register", "registrar",
        "registration", "affairs", "court", "tribunal", "petition", "scheme",
        "arrangement", "amalgamation", "merger", "demerger", "board", "identity",
        "number", "code", "form", "forms", "clause", "section", "schedule",
        "deposit", "guarantee", "account", "accounts", "statement", "statements",
    }
)

#: Words that genuinely end a name when met while walking leftwards. Unlike
#: :data:`~piiredact.lexicons.DESIGNATION_WORDS` as a whole, these never occur
#: inside a company name -- whereas "Management" does, in "KSH Project
#: Management Services Private Limited", and using the full designation list
#: truncated that name to "Services Private Limited".
_EXPANSION_BREAK_WORDS = frozenset(
    {
        "chairman", "chairperson", "director", "directors", "secretary",
        "officer", "officers", "promoter", "promoters", "partner", "partners",
        "proprietor", "trustee", "trustees", "auditor", "auditors",
        "signatory", "shareholder", "shareholders", "designation",
    }
)

#: Legal suffixes that *terminate* a name. Meeting one while expanding leftwards
#: means the previous entity has ended. The weak suffixes are excluded because
#: they occur mid-name all the time -- "Hindalco Industries Limited",
#: "National Securities Depository Limited", "HDFC Bank Limited".
_TERMINATING_SUFFIXES = frozenset(
    norm(s)
    for s in (
        "Limited", "Ltd", "Ltd.", "LLP", "Inc", "Inc.", "Corporation",
        "Incorporated", "Private Limited", "Pvt Ltd", "Pvt. Ltd.",
        "Family Trust", "GmbH", "S.A.", "N.V.",
        "Limited Liability Partnership", "Public Limited Company",
        "Partnership Firm",
    )
)

_MAX_LEFT_TOKENS = 8
_MIN_ALIAS_LEN = 2
#: Punctuation that ends a company name when walking leftwards. A legal-entity
#: name in a filing of this kind never spans a comma, and allowing one lets the
#: detector swallow an entire list -- "HEGDE, RAKHI GIRIJA SHETTY, DHAULAGIRI
#: FAMILY TRUST" was reported as a single organisation before this guard.
_LEFT_STOP_PUNCT = ",;:–—|”’"
#: Stripped from the left of a token before the stop-punctuation test.
_OPENING_PUNCT = "“‘\"'([{"


def _is_org_token(token: str) -> bool:
    return bool(_TOKEN_RE.fullmatch(token))


def _suffix_immediately_left(text: str, position: int) -> bool:
    """True when the word ending just before ``position`` is a legal suffix.

    Used to decide whether an ``and`` joins two words of one name
    ("CARE Analytics and Advisory Private Limited") or two separate entities
    ("HDFC Bank Limited and ICICI Bank Limited").
    """
    match = re.search(r"([A-Za-z.]+)\s*$", text[:position])
    return bool(match) and bool(SUFFIX_RE.fullmatch(match.group(1)))


#: Public-body names, compiled once as an alternation so a candidate overlapping
#: one can be vetoed. Matching against the *text* rather than the candidate is
#: what catches the fragment case: expanding leftwards from "Limited" in
#: "National Securities Depository Limited" stops at the "Securities" terminator
#: and yields "Depository Limited", which no allowlist of whole names would ever
#: contain.
_PUBLIC_BODY_RE = re.compile(
    "|".join(
        re.escape(name)
        for name in sorted(PUBLIC_BODIES, key=len, reverse=True)
        if len(name) >= 8
    ),
    re.IGNORECASE,
)


def trim_leading_noise(name: str) -> str:
    """Apply both leading trims until the name stops changing.

    They feed each other: the generic trim has to remove "the Offer" before the
    role trim can see that what remains starts with "Escrow Collection Bank".
    """
    for _ in range(8):
        trimmed = trim_leading_generic(trim_leading_role_phrase(name))
        if trimmed == name:
            return name
        name = trimmed
    return name


def trim_leading_role_phrase(name: str) -> str:
    """Drop a contractual-role prefix that the expansion swept up.

    "Escrow Collection Bank HDFC Bank" -> "HDFC Bank". The role phrases end in a
    weak suffix, so the expansion legitimately walks straight through them; this
    removes the prefix afterwards rather than complicating the walk.
    """
    tokens = name.split()
    for size in range(min(len(tokens) - 1, 6), 0, -1):
        prefix = " ".join(tokens[:size])
        if norm(prefix) in ORG_ROLE_PHRASES:
            return " ".join(tokens[size:])
    return name


def is_public_or_role(name: str) -> str | None:
    """Return the veto reason when ``name`` must not be redacted, else None."""
    key = norm(name).strip(" .,;:")
    if key in PUBLIC_BODIES:
        return "public-body"
    if key in ORG_ROLE_PHRASES:
        return "contractual-role"
    # "the Stock Exchanges", "our Company" etc. after stripping the article.
    for article in ("the ", "our ", "a ", "an "):
        if key.startswith(article) and key[len(article) :] in (
            PUBLIC_BODIES | ORG_ROLE_PHRASES
        ):
            return "public-body" if key[len(article) :] in PUBLIC_BODIES else "contractual-role"
    return None


def looks_like_org_name(name: str, suffix: str) -> bool:
    """Shape and distinctiveness test for a suffix-anchored candidate."""
    name = name.strip(" ,;:|")
    if len(name) < 4:
        return False
    tokens = name.split()
    if len(tokens) < 2:
        return False
    if is_public_or_role(name):
        return False
    # Tokens to the left of the suffix.
    suffix_tokens = len(suffix.split())
    left = tokens[:-suffix_tokens] if suffix_tokens < len(tokens) else []
    if not left:
        return False
    distinctive = [
        t
        for t in left
        if norm(t.strip("().,&")) not in _INNER_CONNECTIVES
        and norm(t.strip("().,&")) not in DESIGNATION_WORDS
        and norm(t.strip("().,&")) not in PUBLIC_BODY_TOKENS
        and norm(t.strip("().,&")) not in GENERIC_BUSINESS_WORDS
        and len(t.strip("().,&")) > 1
    ]
    if not distinctive:
        return False
    return True


#: Longest an initialism may be before it stops being an initialism and starts
#: being an ordinary word. "KSH" and "MUFG" qualify; "EQUITY" does not.
MAX_ACRONYM_LEN = 5


def trim_leading_generic(name: str) -> str:
    """Drop leading generic words that the left expansion swept up.

    "Corporate Office of our Company KSH International Limited" expands to
    "Company KSH International Limited" because "Company" is a name-shaped token;
    trimming leaves the actual entity. Trimming stops as soon as the remainder
    would no longer be a valid organisation name.
    """
    tokens = name.split()
    while len(tokens) > 2:
        head = norm(tokens[0].strip("().,&"))
        if head not in GENERIC_BUSINESS_WORDS and head not in _INNER_CONNECTIVES:
            break
        candidate = " ".join(tokens[1:])
        suffix = SUFFIX_RE.search(candidate)
        if suffix is None or not looks_like_org_name(candidate, suffix.group(0)):
            break
        tokens = tokens[1:]
    return " ".join(tokens)


def derive_brand_aliases(name: str) -> list[str]:
    """Shorter surface forms of a company name worth sweeping for.

    ``"KSH International Limited"`` -> ``["KSH International", "KSH"]``.
    Only the name-minus-suffix form and a genuine leading initialism are offered.
    The initialism rule is deliberately narrow: an earlier version accepted any
    leading ALL-CAPS token of up to six letters, which turned the heading
    ``EQUITY SHARES ... OF KSH INTERNATIONAL LIMITED`` into an alias ``EQUITY``
    and then redacted 261 occurrences of the ordinary word "equity".
    """
    tokens = name.split()
    aliases: list[str] = []
    # Name minus its legal suffix.
    for suffix in sorted(ORG_SUFFIXES, key=len, reverse=True):
        if norm(name).endswith(norm(suffix)):
            trimmed = name[: len(name) - len(suffix)].strip(" ,.&")
            if len(trimmed.split()) >= 2 and len(trimmed) >= 6:
                aliases.append(trimmed)
            break
    # Leading initialism, e.g. "KSH", "MUFG".
    if tokens:
        head = tokens[0]
        key = norm(head)
        if (
            re.fullmatch(r"[A-Z]{2,%d}" % MAX_ACRONYM_LEN, head)
            and key not in PUBLIC_BODY_TOKENS
            and key not in GENERIC_BUSINESS_WORDS
            and key not in NON_PERSON_PHRASES
        ):
            aliases.append(head)
    # Never offer the name back as its own alias: an alias entry re-entering
    # derivation ("CARE" -> "CARE") would be recorded as self-canonical and lose
    # its link to the parent entity.
    return [a for a in aliases if norm(a) != norm(name)]


@register
class OrganizationDetector(BaseDetector):
    name = "organization.suffix"
    pii_types = (PIIType.ORGANIZATION,)

    def detect(self, segment: Segment, ctx: DetectionContext) -> Iterable[Span]:
        emitted: list[tuple[int, int]] = []

        for span in self._from_suffix(segment):
            if any(s <= span.start and span.end <= e for s, e in emitted):
                continue
            emitted.append((span.start, span.end))
            canonical = span.text
            ctx.gazetteer.add(canonical, PIIType.ORGANIZATION, self.name)
            for alias in derive_brand_aliases(canonical):
                if is_public_or_role(alias):
                    continue
                ctx.gazetteer.add(
                    alias, PIIType.ORGANIZATION, self.name, canonical=canonical
                )
            yield span

        # S2 runs after S1 so the canonical name is already in the gazetteer.
        self._register_defined_aliases(segment, ctx)

    # -- S1: legal-suffix anchoring -----------------------------------------

    def _from_suffix(self, segment: Segment) -> Iterable[Span]:
        text = segment.text
        public = [(m.start(), m.end()) for m in _PUBLIC_BODY_RE.finditer(text)]
        for match in SUFFIX_RE.finditer(text):
            suffix = match.group(0)
            # A legal suffix inside a name is always capitalised. Matching
            # case-insensitively is right for ALL-CAPS headings, but a lowercase
            # hit is ordinary prose -- "Saturdays, Sundays and bank holidays".
            if not suffix[0].isupper():
                continue
            if norm(suffix) in WEAK_ORG_SUFFIXES:
                if self._right_vetoed(text, match.end()):
                    continue
                if not self._left_neighbour_is_distinctive(text, match.start()):
                    continue
            start = self._expand_left(text, match.start())
            end = self._expand_right(text, match.end())
            candidate = text[start:end].strip(" ,;:|")
            if not candidate:
                continue
            candidate = trim_leading_noise(candidate)
            if not candidate:
                continue
            # Recompute exact bounds after trimming.
            found = text.find(candidate, start, end)
            if found < 0:
                continue
            start, end = found, found + len(candidate)
            if not looks_like_org_name(candidate, suffix):
                continue
            if any(ps < end and start < pe for ps, pe in public):
                continue
            yield self.span(
                segment,
                start,
                end,
                PIIType.ORGANIZATION,
                confidence=0.97,
                strategy="legal-suffix",
                suffix=suffix,
            )

    @staticmethod
    def _left_neighbour_is_distinctive(text: str, suffix_start: int) -> bool:
        """Guard for weak suffixes: is there a real proper noun to the left?

        Skips connectives and ampersands, then requires a capitalised,
        non-generic word. This is what separates "HDFC Bank" and "Polycom
        Associates" from "Liabilities of the Company" and "bank holidays".
        """
        words = re.findall(r"[A-Za-z&.'’\-]+", text[:suffix_start])
        for word in reversed(words):
            key = norm(word.strip(".&"))
            if not key or key in _INNER_CONNECTIVES or word == "&":
                continue
            return word[0].isupper() and key not in GENERIC_BUSINESS_WORDS
        return False

    @staticmethod
    def _right_vetoed(text: str, suffix_end: int) -> bool:
        """True when the word after a weak suffix shows it was not a name's end."""
        match = re.match(r"\s*([A-Za-z]+)", text[suffix_end:])
        return match is not None and norm(match.group(1)) in _RIGHT_VETO_WORDS

    @staticmethod
    def _expand_left(text: str, suffix_start: int) -> int:
        """Walk left over name-shaped tokens, returning the name's start offset."""
        cursor = suffix_start
        boundary = cursor
        taken = 0
        while taken < _MAX_LEFT_TOKENS:
            probe = cursor
            # Newlines count as whitespace here. Word wraps a company name across
            # lines inside a table cell, and stopping at the break truncated
            # "Waterloo Industrial Park IX A Private Limited" to
            # "Private Limited".
            while probe > 0 and text[probe - 1] in " \t \n":
                probe -= 1
            if probe == 0:
                break
            token_end = probe
            while probe > 0 and text[probe - 1] not in " \t\n ":
                probe -= 1
            token = text[probe:token_end]
            if not token:
                break
            # A comma, semicolon or closing quote ends the name: the document
            # lists entities and people comma-separated, and crossing one merges
            # them. Leading quotes are stripped first, because every defined term
            # in this prospectus opens with one -- checking the raw token
            # truncated "Bhandary Metal Extrusion Private Limited" to "Metal
            # Extrusion Private Limited".
            if any(char in _LEFT_STOP_PUNCT for char in token.lstrip(_OPENING_PUNCT)):
                break
            bare = token.strip("().,;:’'“”\"")
            key = norm(bare)
            # Connectives are tested first: "and" is both a word that may sit
            # inside a name ("CARE Analytics and Advisory Private Limited") and a
            # word that may not start one, and it appears in both sets. Testing
            # the stop-word set first made every "and" end the expansion, which
            # truncated that name to "Advisory Private Limited".
            if key in _INNER_CONNECTIVES:
                # Allowed inside a name, but not when it joins two entities
                # ("HDFC Bank Limited and ICICI Bank Limited").
                if key == "and" and _suffix_immediately_left(text, probe):
                    break
                cursor = probe
                taken += 1
                continue
            if key in _LEADING_STOP_WORDS:
                break
            if not _is_org_token(bare):
                break
            if key in _EXPANSION_BREAK_WORDS:
                break
            # A *terminating* legal suffix to the left is a different entity:
            # "... Private Limited, Kushal Motors and Electricals Private
            # Limited" is two companies, not one.
            if key in _TERMINATING_SUFFIXES:
                break
            cursor = probe
            boundary = probe
            taken += 1
        return boundary if boundary < suffix_start else suffix_start

    @staticmethod
    def _expand_right(text: str, suffix_end: int) -> int:
        tail = OF_TAIL_RE.match(text[suffix_end:])
        return suffix_end + (tail.end() if tail else 0)

    # -- S2: defined aliases -------------------------------------------------

    def _register_defined_aliases(self, segment: Segment, ctx: DetectionContext) -> None:
        for match in DEFINED_ALIAS_RE.finditer(segment.text):
            name = match.group("name").strip(" ,;:")
            alias = match.group("alias").strip(" ,;:")
            if len(alias) < _MIN_ALIAS_LEN:
                continue
            if is_public_or_role(alias) or is_public_or_role(name):
                continue
            # The defined name must itself be a recognised organisation, so that
            # phrases like `the words "we", "us"` are not picked up.
            canonical_key = ctx.gazetteer.canonical_key(name)
            if canonical_key not in ctx.gazetteer.entries:
                trailing = SUFFIX_RE.search(name)
                if trailing is None or not looks_like_org_name(name, trailing.group(0)):
                    continue
                ctx.gazetteer.add(name, PIIType.ORGANIZATION, self.name)
            entry = ctx.gazetteer.entries.get(ctx.gazetteer.canonical_key(name))
            if entry is None or entry[1] is not PIIType.ORGANIZATION:
                continue
            ctx.gazetteer.add(
                alias, PIIType.ORGANIZATION, f"{self.name}:alias", canonical=name
            )

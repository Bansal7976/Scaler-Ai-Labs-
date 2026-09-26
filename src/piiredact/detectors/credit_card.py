"""Payment card number detection.

Two guards keep this precise in a document full of long digit runs:

1. **Luhn check digit.** Every major card scheme's PAN satisfies the Luhn
   algorithm. A random 16-digit run passes only ~10% of the time, so this alone
   removes most of the noise a length-based regex would create.
2. **Issuer identification number.** The leading digits must match a known
   scheme prefix (Visa, Mastercard, Amex, Discover, JCB, Diners, RuPay,
   Maestro). This removes the remaining coincidental Luhn passes.

A bare, unseparated digit run is additionally required to be preceded by a card
label or to be a 15/16-digit length with a recognised IIN -- again because in a
prospectus an unseparated run of digits is usually a share count.

Like :mod:`piiredact.detectors.ssn`, this type does not occur in the KSH corpus
and is evaluated against the synthetic fixture instead.
"""

from __future__ import annotations

import re
from typing import Iterable

from ..types import PIIType, Segment, Span
from .base import BaseDetector, DetectionContext, register

#: Groups of 4 (and Amex's 4-6-5) with space or hyphen separators, or a bare run.
CANDIDATE_RE = re.compile(
    r"(?<![\d.\-])(?:\d[ \-]?){12,18}\d(?![\d.\-])"
)
LABEL_RE = re.compile(
    r"(?:credit\s*card|debit\s*card|card\s*(?:number|no\.?|#)|PAN\s*number|"
    r"cardholder|visa|mastercard|master\s*card|amex|american\s*express|rupay|"
    r"discover|diners(?:\s*club)?|jcb|maestro)",
    re.IGNORECASE,
)
_LABEL_WINDOW = 40

#: (scheme, accepted lengths, prefix test)
_SCHEMES: tuple[tuple[str, frozenset[int], "re.Pattern[str]"], ...] = (
    ("visa", frozenset({13, 16, 19}), re.compile(r"^4")),
    ("mastercard", frozenset({16}), re.compile(r"^(5[1-5]|2(2[2-9]|[3-6]\d|7[01]|720))")),
    ("amex", frozenset({15}), re.compile(r"^3[47]")),
    ("discover", frozenset({16, 19}), re.compile(r"^(6011|65|64[4-9]|622)")),
    ("diners", frozenset({14, 16, 19}), re.compile(r"^(30[0-5]|3095|36|38|39)")),
    ("jcb", frozenset({16, 17, 18, 19}), re.compile(r"^35(2[89]|[3-8]\d)")),
    ("rupay", frozenset({16}), re.compile(r"^(60|65|81|82|508)")),
    ("maestro", frozenset({12, 13, 14, 15, 16, 17, 18, 19}), re.compile(r"^(5018|5020|5038|56|57|58|6304|6759|676[1-3])")),
)


def luhn_ok(digits: str) -> bool:
    """True when ``digits`` satisfies the Luhn (mod-10) check."""
    if not digits.isdigit() or len(digits) < 12:
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


#: An unseparated, unlabelled run must be at least this varied to be a card.
MIN_DISTINCT_DIGITS = 4
#: A run of this many identical digits marks a rounded amount, not a PAN.
MAX_IDENTICAL_RUN = 6


def has_card_like_entropy(digits: str) -> bool:
    """Reject rounded numbers that happen to be Luhn-valid."""
    if len(set(digits)) < MIN_DISTINCT_DIGITS:
        return False
    run = best = 1
    for previous, current in zip(digits, digits[1:]):
        run = run + 1 if current == previous else 1
        best = max(best, run)
    return best < MAX_IDENTICAL_RUN


def identify_scheme(digits: str) -> str | None:
    """Return the card scheme whose IIN and length ``digits`` matches."""
    for scheme, lengths, prefix in _SCHEMES:
        if len(digits) in lengths and prefix.match(digits):
            return scheme
    return None


@register
class CreditCardDetector(BaseDetector):
    name = "credit_card.luhn"
    pii_types = (PIIType.CREDIT_CARD,)

    def detect(self, segment: Segment, ctx: DetectionContext) -> Iterable[Span]:
        text = segment.text
        for match in CANDIDATE_RE.finditer(text):
            raw = match.group(0)
            digits = re.sub(r"\D", "", raw)
            if not luhn_ok(digits):
                continue
            scheme = identify_scheme(digits)
            if scheme is None:
                continue
            separated = bool(re.search(r"[ \-]", raw))
            labelled = bool(
                LABEL_RE.search(text[max(0, match.start() - _LABEL_WINDOW) : match.start()])
            )
            if not separated and not labelled:
                # Unseparated and unlabelled: too easily a share count or a
                # rounded amount. Require Luhn and a scheme prefix, which we
                # have, plus a common length and enough digit variety that the
                # run is not obviously a round number. "4200000000000000" is
                # Luhn-valid and Visa-prefixed, and is a share count.
                if len(digits) not in (15, 16):
                    continue
                if not has_card_like_entropy(digits):
                    continue
            yield self.span(
                segment,
                match.start(),
                match.end(),
                PIIType.CREDIT_CARD,
                confidence=1.0 if (separated or labelled) else 0.85,
                scheme=scheme,
                separated=separated,
                labelled=labelled,
            )

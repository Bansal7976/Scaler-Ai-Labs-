"""IP address detection (IPv4 and IPv6).

The trap here is version strings and dotted numbering: ``Ind AS 115.2.1.3``,
``clause 4.1.2.3``, ``10.20.30.40 million``. Guards:

* every octet is validated in range 0-255 via :mod:`ipaddress`, not by regex;
* a match immediately preceded by a version/clause cue (``v``, ``version``,
  ``clause``, ``paragraph``, ``AS``) or immediately followed by a unit word is
  rejected;
* a match whose neighbouring character is a digit or another dot is rejected, so
  ``1.2.3.4.5`` does not yield ``1.2.3.4``.

Private, loopback, link-local and documentation ranges are still reported, but
they are flagged in the audit log: they identify infrastructure rather than a
person, and a reviewer may reasonably choose to leave them. The default policy
is to redact them, and that default is stated in the README.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Iterable

from ..types import PIIType, Segment, Span
from .base import BaseDetector, DetectionContext, register

# The trailing guard rejects a following digit or dotted group -- so
# "1.2.3.4.5" does not yield "1.2.3.4" -- but must *not* reject an ordinary
# sentence-ending period, which an earlier `(?![\w.])` did, silently losing
# every address at the end of a sentence.
IPV4_RE = re.compile(r"(?<![\w.])(?<!\d\.)\d{1,3}(?:\.\d{1,3}){3}(?!\.?\d)(?![\w])")
# Candidate-then-validate: the regex only has to recognise something
# colon-separated and hex-only (which excludes "12:30:45" timestamps by the
# group count), and :mod:`ipaddress` decides whether it is a real address. A
# regex strict enough to handle "::" compression on its own is unreadable and was
# in fact wrong -- it missed "2001:db8:85a3::8a2e:370:7334".
IPV6_RE = re.compile(
    r"(?<![\w:.])[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7}(?:/\d{1,3})?(?![\w:])"
)

VERSION_CUE_RE = re.compile(
    r"(?:\bv|\bversion|\bclause|\bparagraph|\bsection|\brule|\bIND?\s*AS|\bIAS|"
    r"\bIFRS|\bpage|\brelease|\bbuild)\s*$",
    re.IGNORECASE,
)
UNIT_FOLLOW_RE = re.compile(
    r"^\s*(?:million|billion|crore|lakh|%|per\s*cent|percent|times|x\b)",
    re.IGNORECASE,
)
_CUE_WINDOW = 14


@register
class IPAddressDetector(BaseDetector):
    name = "ip.pattern"
    pii_types = (PIIType.IP_ADDRESS,)

    def detect(self, segment: Segment, ctx: DetectionContext) -> Iterable[Span]:
        text = segment.text
        for match in IPV4_RE.finditer(text):
            raw = match.group(0)
            try:
                address = ipaddress.IPv4Address(raw)
            except ValueError:
                continue
            if self._vetoed(text, match.start(), match.end()):
                continue
            yield self.span(
                segment,
                match.start(),
                match.end(),
                PIIType.IP_ADDRESS,
                confidence=1.0,
                version=4,
                is_private=address.is_private,
                is_global=address.is_global,
            )

        for match in IPV6_RE.finditer(text):
            raw = match.group(0)
            try:
                address = ipaddress.ip_address(raw.split("/", 1)[0])
            except ValueError:
                continue
            if address.version != 6:
                continue
            if self._vetoed(text, match.start(), match.end()):
                continue
            yield self.span(
                segment,
                match.start(),
                match.end(),
                PIIType.IP_ADDRESS,
                confidence=0.95,
                version=6,
                is_private=address.is_private,
            )

    @staticmethod
    def _vetoed(text: str, start: int, end: int) -> bool:
        before = text[max(0, start - _CUE_WINDOW) : start]
        if VERSION_CUE_RE.search(before):
            return True
        if UNIT_FOLLOW_RE.match(text[end : end + 16]):
            return True
        return False

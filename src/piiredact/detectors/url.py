"""Website and URL detection.

A corporate website is a direct re-identifier: ``www.kshinternational.com``
gives away the issuer's name even after every textual mention of it has been
replaced. So URLs belonging to redacted parties are redacted, and URLs belonging
to public bodies (regulators, exchanges, government) are kept -- the same policy
line drawn for organisation names, applied to the domain instead of the name.

Detection is regex; the decision is entirely in :data:`PUBLIC_DOMAIN_SUFFIXES`.
"""

from __future__ import annotations

import re
from typing import Iterable

from ..types import PIIType, Segment, Span
from .base import BaseDetector, DetectionContext, register

# ``[ ]?`` after each dot is not sloppiness: Word's justified text in this
# prospectus stores the cover-page website as "www.kshinternational. com", and a
# regex without it leaves the issuer's domain in the redacted document.
URL_RE = re.compile(
    r"""
    (?<![\w@./-])
    (?:
        (?:https?://|ftp://)[^\s<>"'|\])}]+
      | www\.[ ]?[A-Za-z0-9\-]+(?:\.[ ]?[A-Za-z0-9\-]+)+(?:/[^\s<>"'|\])}]*)?
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

#: Domains (matched as suffixes) that identify a public body or public
#: infrastructure and are therefore preserved verbatim.
PUBLIC_DOMAIN_SUFFIXES: tuple[str, ...] = (
    ".gov.in",
    ".gov",
    ".nic.in",
    ".gov.uk",
    "sebi.gov.in",
    "rbi.org.in",
    "bseindia.com",
    "nseindia.com",
    "nsdl.com",
    "nsdl.co.in",
    "cdslindia.com",
    "npci.org.in",
    "mca.gov.in",
    "icai.org",
    "irdai.gov.in",
    "fbil.org.in",
    "sec.gov",
    "ifrs.org",
    "oanda.com",
    "wikipedia.org",
)

_TRAILING_PUNCT = ".,;:!?)’”'\"]}"


def _host_of(url: str) -> str:
    host = re.sub(r"^\w+://", "", url, flags=re.IGNORECASE)
    host = host.replace(" ", "")  # undo the justified-text space described above
    host = host.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    return host.lower().removeprefix("www.")


def is_public_domain(url: str) -> bool:
    host = _host_of(url)
    return any(
        host == suffix.lstrip(".") or host.endswith(suffix)
        for suffix in PUBLIC_DOMAIN_SUFFIXES
    )


@register
class UrlDetector(BaseDetector):
    name = "url.regex"
    pii_types = (PIIType.URL,)

    def detect(self, segment: Segment, ctx: DetectionContext) -> Iterable[Span]:
        for match in URL_RE.finditer(segment.text):
            start, end = match.start(), match.end()
            while end > start and segment.text[end - 1] in _TRAILING_PUNCT:
                end -= 1
            url = segment.text[start:end]
            if not url or is_public_domain(url):
                continue
            ctx.gazetteer.add(url, PIIType.URL, self.name)
            yield self.span(
                segment,
                start,
                end,
                PIIType.URL,
                confidence=1.0,
                host=_host_of(url),
            )

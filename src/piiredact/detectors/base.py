"""Detector protocol, shared context, and the detector registry.

Detection runs in two passes and detectors declare which they take part in:

**Pass 1 -- local detection.** Every detector sees each segment in isolation and
emits the spans it is confident about. Detectors that recognise *entities*
(people, organisations) also register the surface forms they found in the shared
:class:`Gazetteer`.

**Pass 2 -- gazetteer sweep.** Now that the gazetteer knows every entity named
anywhere in the document, the whole document is re-scanned for those names and
for their derived short forms ("Kushal Subbayya Hegde" -> "Mr. Hegde",
"KSH International Limited" -> "KSH International"). This is what turns a
high-precision, low-recall first pass into high recall without loosening any of
the rules that gave the precision.

Registering a detector is a decorator call; see :func:`register`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Iterable, Iterator, Protocol, Sequence

from ..lexicons import norm
from ..types import PIIType, Segment, Span

if TYPE_CHECKING:  # pragma: no cover - typing only
    from spacy.language import Language


class Pass(int):
    """Marker for which detection pass a detector runs in."""


PASS_LOCAL = 1
PASS_GAZETTEER = 2


@dataclass
class Gazetteer:
    """Document-wide memory of entity surface forms found in pass 1.

    Keys are normalised surface forms; values are the canonical (first-seen,
    longest) spelling and the PII type. Keeping the canonical spelling lets the
    surrogate engine assign one fake identity per real entity, so that every
    mention of the same person is replaced by the same pseudonym.
    """

    #: normalised surface form -> (canonical spelling, PII type)
    entries: dict[str, tuple[str, PIIType]] = field(default_factory=dict)
    #: normalised surface form -> normalised canonical form it belongs to
    aliases: dict[str, str] = field(default_factory=dict)
    #: provenance, for the audit log: normalised form -> detectors that found it
    sources: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    def add(
        self,
        surface: str,
        pii_type: PIIType,
        detector: str,
        canonical: str | None = None,
    ) -> str:
        """Record ``surface`` as a known entity. Returns its normalised key."""
        surface = surface.strip()
        if not surface:
            return ""
        key = norm(surface)
        canon_key = norm(canonical) if canonical else key
        existing = self.entries.get(key)
        if existing is None or len(surface) > len(existing[0]):
            self.entries[key] = (surface, pii_type)
        self.sources[key].add(detector)
        # An entity can be found both standalone and as the prefix of a longer
        # name: the suffix detector reports "ICICI Bank" from the weak "Bank"
        # anchor and "ICICI Bank Limited" from "Limited". Whichever arrives first
        # must not win, or the two forms get unrelated surrogates and read as two
        # different banks. A self-canonical entry is therefore allowed to be
        # re-rooted onto a longer parent, but never the other way round.
        current = self.aliases.get(key)
        if current is None or (current == key and canon_key != key):
            self.aliases[key] = canon_key
        if canonical:
            # Make sure the canonical form is itself present.
            canon = canonical.strip()
            prev = self.entries.get(canon_key)
            if prev is None or len(canon) > len(prev[0]):
                self.entries[canon_key] = (canon, pii_type)
            # Only claim self-canonical status if nothing better is known. An
            # entity's alias can be re-offered as its own canonical by a later
            # derivation pass ("CARE" -> "CARE"), and overwriting here would cut
            # the alias loose from its parent and give it a separate surrogate.
            if self.aliases.get(canon_key, canon_key) == canon_key:
                self.aliases[canon_key] = canon_key
        return key

    def canonical_key(self, surface: str) -> str:
        """Resolve a surface form to the key of the entity it names."""
        key = norm(surface)
        seen: set[str] = set()
        while key in self.aliases and self.aliases[key] != key and key not in seen:
            seen.add(key)
            key = self.aliases[key]
        return key

    def canonical_text(self, surface: str) -> str:
        key = self.canonical_key(surface)
        entry = self.entries.get(key)
        return entry[0] if entry else surface.strip()

    def of_type(self, pii_type: PIIType) -> dict[str, str]:
        """All known surface forms of one type: normalised key -> spelling."""
        return {k: v[0] for k, v in self.entries.items() if v[1] is pii_type}

    def __contains__(self, surface: object) -> bool:
        return isinstance(surface, str) and norm(surface) in self.entries

    def __len__(self) -> int:
        return len(self.entries)


@dataclass
class DetectionContext:
    """Everything a detector may read beyond the segment it is given."""

    gazetteer: Gazetteer = field(default_factory=Gazetteer)
    #: Loaded spaCy pipeline, or ``None`` when NER is disabled.
    nlp: "Language | None" = None
    #: All segments of the document, for detectors that need cross-segment view.
    segments: Sequence[Segment] = ()
    #: Pre-computed spaCy docs keyed by segment key (filled by the pipeline).
    ner_cache: dict[str, list[tuple[int, int, str, str]]] = field(default_factory=dict)
    #: Minimum confidence a span must carry to survive.
    min_confidence: float = 0.0

    def ner_entities(self, segment: Segment) -> list[tuple[int, int, str, str]]:
        """``(start, end, text, label)`` for one segment, from the NER cache."""
        return self.ner_cache.get(segment.key, [])


class Detector(Protocol):
    """The whole detector contract.

    A detector is any object with a ``name``, the ``pii_types`` it can emit, the
    ``pass_number`` it runs in, and a ``detect`` method that yields spans for one
    segment. Detectors must be stateless with respect to the document: anything
    they want to remember goes in the shared :class:`Gazetteer`, so that a run is
    reproducible and the order of segments does not change the outcome of pass 2.
    """

    name: str
    pii_types: tuple[PIIType, ...]
    pass_number: int

    def detect(self, segment: Segment, ctx: DetectionContext) -> Iterable[Span]:
        ...


class BaseDetector:
    """Convenience base that supplies the boilerplate parts of the protocol."""

    name: str = "base"
    pii_types: tuple[PIIType, ...] = ()
    pass_number: int = PASS_LOCAL
    #: Set by subclasses that want to be skipped unless explicitly enabled.
    experimental: bool = False

    def detect(self, segment: Segment, ctx: DetectionContext) -> Iterable[Span]:
        raise NotImplementedError

    # -- helpers shared by concrete detectors --------------------------------

    def span(
        self,
        segment: Segment,
        start: int,
        end: int,
        pii_type: PIIType,
        confidence: float = 1.0,
        **evidence: object,
    ) -> Span:
        """Build a :class:`Span`, trimming surrounding punctuation/whitespace."""
        text = segment.text[start:end]
        # Quotes and brackets are never part of a value. A left-expanding
        # detector routinely stops just outside one -- the prospectus writes
        # every defined term as "KSH International Limited" -- and leaving the
        # quote inside the span would replace it along with the name.
        lead = len(text) - len(
            text.lstrip(" \t\n\r “”‘’\"'([{)]}")
        )
        start += lead
        text = text[lead:]
        # Footnote markers (*, #, ^, dagger) trail names all through this
        # document's tables; leaving one inside the span makes
        # "Kushal Subbayya Hegde*" a different gazetteer entity from
        # "Kushal Subbayya Hegde".
        trail = len(text) - len(
            text.rstrip(" \t .,;:|-–—“”‘’\"')]}*#^~†‡")
        )
        end -= trail
        text = segment.text[start:end]
        return Span(
            start=start,
            end=end,
            text=text,
            pii_type=pii_type,
            detector=self.name,
            confidence=confidence,
            evidence=dict(evidence),
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} {self.name} pass={self.pass_number}>"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
_REGISTRY: dict[str, Detector] = {}


def register(detector_cls: Callable[[], Detector]) -> Callable[[], Detector]:
    """Class decorator: instantiate a detector and add it to the registry.

    Detectors are registered at import time by :mod:`piiredact.detectors`.
    """
    instance = detector_cls()
    if instance.name in _REGISTRY:
        raise ValueError(f"duplicate detector name: {instance.name!r}")
    _REGISTRY[instance.name] = instance
    return detector_cls


def all_detectors() -> list[Detector]:
    """Registered detectors, ordered by pass then name for reproducibility."""
    return sorted(_REGISTRY.values(), key=lambda d: (d.pass_number, d.name))


def detectors_for_pass(pass_number: int) -> list[Detector]:
    return [d for d in all_detectors() if d.pass_number == pass_number]


def detectors_for_types(types: Iterable[PIIType]) -> list[Detector]:
    wanted = set(types)
    return [d for d in all_detectors() if wanted.intersection(d.pii_types)]


def iter_registry() -> Iterator[tuple[str, Detector]]:
    yield from sorted(_REGISTRY.items())

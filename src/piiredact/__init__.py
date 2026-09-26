"""piiredact -- detect PII in a .docx and replace it with consistent fake values.

Typical use::

    from piiredact import Config, redact_document

    result = redact_document("in.docx", "out.docx", Config())
    print(result.detection.type_counts)

The pipeline is described in ``README.md``; the short version is that twelve
detectors run in two passes, their overlapping claims are reduced to a disjoint
plan, each surviving span is given a consistent surrogate, and the surrogates are
written back into the document's original runs so formatting survives.
"""

from .config import Config, DEFAULT_CONFIG
from .types import PIIType, Segment, Span

__all__ = ["Config", "DEFAULT_CONFIG", "PIIType", "Segment", "Span", "__version__"]
__version__ = "1.0.0"


def __getattr__(name: str):
    # Imported lazily so that ``import piiredact`` does not pull in spaCy/Faker.
    if name in ("redact_document", "detect_spans", "RedactionResult", "DetectionResult"):
        from . import pipeline

        return getattr(pipeline, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

"""Run configuration.

Everything tunable lives here as one frozen dataclass, so a run is fully
described by its config plus its input file. The config is written into
``run_summary.json`` next to the output, which is what makes a result
reproducible: re-running with the same config and seed produces a byte-identical
redacted document.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable

from .types import PIIType

#: The types the assignment requires at minimum.
REQUIRED_TYPES: frozenset[PIIType] = frozenset(
    {
        PIIType.PERSON,
        PIIType.EMAIL,
        PIIType.PHONE,
        PIIType.ORGANIZATION,
        PIIType.ADDRESS,
        PIIType.SSN,
        PIIType.CREDIT_CARD,
        PIIType.DOB,
        PIIType.IP_ADDRESS,
    }
)

#: Types this tool adds on top, on the argument set out in
#: :mod:`piiredact.detectors.national_id`: leaving a unique key next to a
#: redacted name is not a redaction.
EXTENDED_TYPES: frozenset[PIIType] = frozenset(
    {PIIType.NATIONAL_ID, PIIType.URL}
)

DEFAULT_TYPES: frozenset[PIIType] = REQUIRED_TYPES | EXTENDED_TYPES

#: spaCy model used for the NER strategy. ``md`` rather than ``sm`` because on
#: this corpus ``sm`` misses several directors that ``md`` finds, and ``trf``
#: needs a GPU to be practical over 3,700 segments.
DEFAULT_SPACY_MODEL = "en_core_web_md"


@dataclass(frozen=True, slots=True)
class Config:
    """One redaction run's settings."""

    #: PII types to detect and replace.
    types: frozenset[PIIType] = DEFAULT_TYPES
    #: Seed for surrogate generation. Same seed -> same fake values.
    seed: str = "scaler-ai-labs-2026"
    #: Faker locale for names and places.
    locale: str = "en_IN"
    #: Drop any span below this confidence before replacement.
    min_confidence: float = 0.60
    #: spaCy model name, or None to run without the NER strategy.
    spacy_model: str | None = DEFAULT_SPACY_MODEL
    #: Run the pass-2 gazetteer sweep.
    enable_gazetteer_sweep: bool = True
    #: ``"labelled"`` (precision-first) or ``"contextual"`` for the DOB detector.
    dob_mode: str = "labelled"
    #: Blank author/company metadata in the output document.
    clear_metadata: bool = True
    #: Re-scan the written document and report any original value still present.
    verify_output: bool = True
    #: Detector names to switch off, for ablation studies.
    disabled_detectors: frozenset[str] = field(default_factory=frozenset)

    def with_types(self, types: Iterable[PIIType]) -> "Config":
        return replace(self, types=frozenset(types))

    def without(self, *detector_names: str) -> "Config":
        return replace(
            self, disabled_detectors=self.disabled_detectors | frozenset(detector_names)
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "types": sorted(str(t) for t in self.types),
            "seed": self.seed,
            "locale": self.locale,
            "min_confidence": self.min_confidence,
            "spacy_model": self.spacy_model,
            "enable_gazetteer_sweep": self.enable_gazetteer_sweep,
            "dob_mode": self.dob_mode,
            "clear_metadata": self.clear_metadata,
            "verify_output": self.verify_output,
            "disabled_detectors": sorted(self.disabled_detectors),
        }


#: Preset used for the graded run and for the evaluation report.
DEFAULT_CONFIG = Config()

#: Preset for the ablation that measures what the gazetteer sweep is worth.
NO_SWEEP_CONFIG = Config(enable_gazetteer_sweep=False)

#: Preset for the ablation that measures what spaCy NER is worth.
NO_NER_CONFIG = Config(spacy_model=None)


__all__ = [
    "Config",
    "DEFAULT_CONFIG",
    "DEFAULT_SPACY_MODEL",
    "DEFAULT_TYPES",
    "EXTENDED_TYPES",
    "NO_NER_CONFIG",
    "NO_SWEEP_CONFIG",
    "REQUIRED_TYPES",
]

"""Detector package.

Importing this package registers every detector. To add a PII type:

1. add the member to :class:`piiredact.types.PIIType` and give it a priority in
   :data:`piiredact.types.TYPE_PRIORITY`;
2. write a module here whose detector class carries ``@register``;
3. import it below;
4. add a surrogate rule in :mod:`piiredact.surrogates`.

Nothing else in the pipeline needs to change -- detection, overlap resolution,
replacement, the audit log and the evaluation harness all read the type from the
span.
"""

from __future__ import annotations

from .base import (  # noqa: F401  (re-exported as the package's public surface)
    PASS_GAZETTEER,
    PASS_LOCAL,
    BaseDetector,
    DetectionContext,
    Detector,
    Gazetteer,
    all_detectors,
    detectors_for_pass,
    detectors_for_types,
    iter_registry,
    register,
)

# Import order is irrelevant to behaviour -- detectors are sorted by pass and
# name in the registry -- but it is kept in "structured first, fuzzy last" order
# to match how the README describes the pipeline.
from . import email  # noqa: F401,E402
from . import url  # noqa: F401,E402
from . import phone  # noqa: F401,E402
from . import ssn  # noqa: F401,E402
from . import credit_card  # noqa: F401,E402
from . import ip_address  # noqa: F401,E402
from . import national_id  # noqa: F401,E402
from . import dob  # noqa: F401,E402
from . import address  # noqa: F401,E402
from . import organization  # noqa: F401,E402
from . import person  # noqa: F401,E402
from . import gazetteer  # noqa: F401,E402

__all__ = [
    "PASS_GAZETTEER",
    "PASS_LOCAL",
    "BaseDetector",
    "DetectionContext",
    "Detector",
    "Gazetteer",
    "all_detectors",
    "detectors_for_pass",
    "detectors_for_types",
    "iter_registry",
    "register",
]

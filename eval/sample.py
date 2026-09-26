"""Build the evaluation sample from the source document.

Selection is deterministic (fixed seed, fixed rules) so the gold standard can be
regenerated and re-checked by anyone. It is *stratified* rather than uniform,
because a uniform sample of a 122-page prospectus is almost all financial tables
and would measure precision on negatives while saying nothing about recall.

Four strata:

``structural``  the PII-dense tables and cover-page blocks -- the cover table,
                the board-of-directors table, the underwriters table, and the
                "General Information" contact blocks. Chosen in full, because
                these are where recall is decided.
``contact``     any other segment containing an email, a telephone label or a
                postal-code-shaped string. Chosen in full.
``prose``       a random sample of body paragraphs from the risk factors and
                business sections -- where names appear in running text.
``negative``    a random sample of segments from the financial tables, which
                should contain no PII at all. These carry the precision signal:
                long digit runs, dates and capitalised jargon.

Run with ``python eval/sample.py`` to (re)write ``eval/sample.json``.
"""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from piiredact.docio import read_document  # noqa: E402

_LOCAL = ROOT / "data" / "Red Herring Prospectus.docx"
SOURCE = _LOCAL if _LOCAL.exists() else ROOT.parent / "Red Herring Prospectus.docx"
OUTPUT = Path(__file__).resolve().parent / "sample.json"

SEED = 20260925
#: Tables chosen in full because they are the document's PII-bearing structures.
STRUCTURAL_TABLES = (0, 2, 70, 74)
#: Paragraph indices of the cover page and General Information contact blocks.
STRUCTURAL_PARAGRAPHS = tuple(range(23, 33))

PROSE_SAMPLE_SIZE = 40
NEGATIVE_SAMPLE_SIZE = 45

#: Segments always included, whatever the random draw returns. These three were
#: drawn into the original ``negative`` sample and turned out to carry PII in a
#: table the stratification rules do not otherwise reach (shareholder-name
#: columns in the capital-structure tables). Pinning them keeps the sample -- and
#: therefore the gold standard -- stable across re-sampling.
PINNED_KEYS: tuple[str, ...] = (
    "body/tbl23/r10c1",
    "body/tbl25/r11c0",
    "body/tbl37/r5c1",
)

_EMAIL_HINT = re.compile(r"[\w.+-]+@[\w.-]+\.\w{2,}")
_PHONE_HINT = re.compile(r"(?:Telephone|Tel\.?|Mobile|Fax)\s*[:\-]", re.IGNORECASE)
_PIN_HINT = re.compile(r"[–\-,]\s*[1-9]\d{2}\s?\d{3}\b")
_CONTACT_HINT = re.compile(r"Contact Person|Registered Office|Corporate Office|DIN\b")


def classify(segment) -> str | None:
    """Assign a segment to a stratum, or None to leave it out of the sample."""
    if segment.table_index in STRUCTURAL_TABLES:
        return "structural"
    if segment.kind == "paragraph":
        match = re.fullmatch(r"paragraph (\d+)", segment.location)
        if match and int(match.group(1)) in STRUCTURAL_PARAGRAPHS:
            return "structural"
    text = segment.text
    if _EMAIL_HINT.search(text) or _PHONE_HINT.search(text) or _CONTACT_HINT.search(text):
        return "contact"
    if _PIN_HINT.search(text):
        return "contact"
    if segment.kind == "paragraph" and len(text) >= 120:
        return "prose"
    if segment.is_table_cell and len(text) >= 8:
        return "negative"
    return None


def build() -> dict:
    read = read_document(str(SOURCE))
    buckets: dict[str, list] = {"structural": [], "contact": [], "prose": [], "negative": []}
    for segment in read.segments:
        stratum = classify(segment)
        if stratum:
            buckets[stratum].append(segment)

    rng = random.Random(SEED)
    chosen: list[tuple[str, object]] = []
    for stratum in ("structural", "contact"):
        chosen.extend((stratum, s) for s in buckets[stratum])
    for stratum, size in (("prose", PROSE_SAMPLE_SIZE), ("negative", NEGATIVE_SAMPLE_SIZE)):
        pool = sorted(buckets[stratum], key=lambda s: s.key)
        picked = rng.sample(pool, min(size, len(pool)))
        pinned = [s for s in pool if s.key in PINNED_KEYS and s not in picked]
        chosen.extend((stratum, s) for s in picked + pinned)

    # Stable order: document order, which is the order of read.segments.
    order = {segment.key: index for index, segment in enumerate(read.segments)}
    chosen.sort(key=lambda pair: order[pair[1].key])

    return {
        "source": SOURCE.name,
        "seed": SEED,
        "strata_sizes": {k: len(v) for k, v in buckets.items()},
        "sample_size": len(chosen),
        "segments": [
            {
                "key": segment.key,
                "stratum": stratum,
                "location": segment.location,
                "kind": segment.kind,
                "section": segment.section,
                "column_header": segment.column_header,
                "text": segment.text,
            }
            for stratum, segment in chosen
        ],
    }


def main() -> int:
    payload = build()
    OUTPUT.write_text(
        json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(f"wrote {OUTPUT} -- {payload['sample_size']} segments")
    for stratum, size in payload["strata_sizes"].items():
        picked = sum(1 for s in payload["segments"] if s["stratum"] == stratum)
        print(f"  {stratum:12} {picked:>4} of {size:>5} available")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

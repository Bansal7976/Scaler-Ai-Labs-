"""Writing redactions back into a .docx without losing formatting.

The naive approach -- set ``paragraph.text = redacted`` -- destroys every run in
the paragraph and with it all bold, italics, font, size, colour, hyperlinks and
language marks. In a prospectus that is not cosmetic damage: the cover page,
the table headers and the defined terms all rely on run formatting, and a
reviewer comparing the two documents side by side would see a different
document rather than a redacted one.

So replacement happens **inside the runs**. For each span:

* find every run the span touches, using the offset map built by the reader;
* write the whole surrogate into the *first* touched run, at the right offset
  within that run's own text;
* delete the span's characters from the remaining touched runs, leaving whatever
  those runs held outside the span.

The surrogate therefore inherits the formatting of the run where the value
started, which is what a human redactor would do by selecting the text and
typing over it.

Spans are applied right to left so that earlier offsets stay valid, which is why
:func:`piiredact.resolve.resolve` guarantees a disjoint, sorted plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from docx.document import Document as DocxDocument

from ..types import Span
from .docx_reader import PARAGRAPH_SEPARATOR, SegmentHandle


@dataclass(frozen=True, slots=True)
class Replacement:
    """One span and the text that should take its place."""

    span: Span
    surrogate: str


@dataclass
class WriteStats:
    replacements_applied: int = 0
    replacements_skipped: int = 0
    runs_modified: int = 0
    segments_modified: int = 0
    characters_removed: int = 0
    characters_inserted: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "replacements_applied": self.replacements_applied,
            "replacements_skipped": self.replacements_skipped,
            "runs_modified": self.runs_modified,
            "segments_modified": self.segments_modified,
            "characters_removed": self.characters_removed,
            "characters_inserted": self.characters_inserted,
        }


def apply_replacements(
    handle: SegmentHandle,
    replacements: Sequence[Replacement],
    stats: WriteStats | None = None,
) -> WriteStats:
    """Rewrite one segment's runs in place.

    ``replacements`` must be disjoint; they are sorted and applied last-first.
    """
    stats = stats if stats is not None else WriteStats()
    if not replacements:
        return stats

    ordered = sorted(replacements, key=lambda r: r.span.start, reverse=True)
    modified_runs: set[int] = set()

    for replacement in ordered:
        span = replacement.span
        touched = [
            ref
            for ref in handle.runs
            if ref.start < span.end and span.start < ref.end
        ]
        if not touched:
            stats.replacements_skipped += 1
            continue
        # A span crossing a paragraph boundary would have swallowed the
        # separator; the surrogate goes into the first paragraph and the tail is
        # removed, which keeps the paragraph structure intact.
        written = False
        for ref in touched:
            run_text = ref.run.text
            local_start = max(0, span.start - ref.start)
            local_end = min(len(run_text), span.end - ref.start)
            if local_end <= local_start:
                continue
            removed = local_end - local_start
            insert = replacement.surrogate if not written else ""
            ref.run.text = run_text[:local_start] + insert + run_text[local_end:]
            stats.characters_removed += removed
            stats.characters_inserted += len(insert)
            modified_runs.add(id(ref.run._r))
            written = True
        if written:
            stats.replacements_applied += 1
        else:  # pragma: no cover - touched implies at least one overlap
            stats.replacements_skipped += 1

    stats.runs_modified += len(modified_runs)
    if modified_runs:
        stats.segments_modified += 1
    # The offset map is now stale; callers must re-read if they need it again.
    return stats


def apply_document(
    handles_by_key: dict[str, SegmentHandle],
    plan: dict[str, Sequence[Replacement]],
) -> WriteStats:
    """Apply a whole document's replacement plan, segment by segment."""
    stats = WriteStats()
    for key, replacements in plan.items():
        handle = handles_by_key.get(key)
        if handle is None:  # pragma: no cover - keys come from the same read
            stats.replacements_skipped += len(replacements)
            continue
        apply_replacements(handle, replacements, stats)
    return stats


def save_document(document: DocxDocument, path: str) -> None:
    """Write the modified document out."""
    document.save(path)


def clear_metadata(document: DocxDocument) -> dict[str, str]:
    """Blank the core properties that can carry identity, returning what was set.

    A .docx keeps the author, last-editor, company and manager in
    ``docProps/core.xml`` and ``docProps/app.xml``. Redacting the body text and
    shipping the file with the drafting lawyer's name in its properties would
    defeat the exercise, so the properties are cleared as part of the run.
    """
    core = document.core_properties
    cleared: dict[str, str] = {}
    for field_name in (
        "author",
        "last_modified_by",
        "title",
        "subject",
        "comments",
        "category",
        "keywords",
        "identifier",
        "content_status",
    ):
        value = getattr(core, field_name, None)
        if value:
            cleared[field_name] = str(value)
        try:
            setattr(core, field_name, "")
        except (AttributeError, ValueError):  # pragma: no cover - read-only props
            continue
    return cleared


def verify_applied(
    handle: SegmentHandle, replacements: Iterable[Replacement]
) -> list[str]:
    """Post-write check: report any original value still present in the segment.

    Called by the pipeline's integrity check. Re-reads the live run text rather
    than trusting the write, because a stale offset map is the most likely way
    for a redaction to silently fail.
    """
    current = PARAGRAPH_SEPARATOR.join(
        "".join(
            ref.run.text
            for ref in handle.runs
            if ref.paragraph_index == paragraph_index
        )
        for paragraph_index in range(len(handle.paragraphs))
    )
    leaks = []
    for replacement in replacements:
        original = replacement.span.text.strip()
        if len(original) >= 4 and original in current:
            leaks.append(original)
    return leaks


__all__ = [
    "Replacement",
    "WriteStats",
    "apply_replacements",
    "apply_document",
    "save_document",
    "clear_metadata",
    "verify_applied",
]

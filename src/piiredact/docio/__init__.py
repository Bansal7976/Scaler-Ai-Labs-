"""Document I/O. Currently .docx only; the pipeline talks to this package
through :func:`read_document` and :func:`apply_document`, so adding another
container format means adding a module here and nothing else."""

from .docx_reader import ReadResult, RunRef, SegmentHandle, read_document
from .docx_writer import (
    Replacement,
    WriteStats,
    apply_document,
    apply_replacements,
    clear_metadata,
    save_document,
    verify_applied,
)

__all__ = [
    "ReadResult",
    "Replacement",
    "RunRef",
    "SegmentHandle",
    "WriteStats",
    "apply_document",
    "apply_replacements",
    "clear_metadata",
    "read_document",
    "save_document",
    "verify_applied",
]

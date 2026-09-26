"""Render ``EVALUATION_REPORT.md`` as a Word document.

The submission form takes an uploaded file rather than a link, so the report
ships as .docx alongside the Markdown. This is a small, purpose-built converter:
it handles exactly the constructs the report uses -- ATX headings, paragraphs,
bullet lists, pipe tables, horizontal rules, and inline ``code``/**bold**/*italic*
-- rather than pulling in a general Markdown engine for six features.
"""

from __future__ import annotations

import re
from pathlib import Path

import docx
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "EVALUATION_REPORT.md"
OUTPUT = ROOT / "EVALUATION_REPORT.docx"

ACCENT = RGBColor(0x0D, 0x6B, 0x6B)
INK = RGBColor(0x13, 0x20, 0x2A)
MUTED = RGBColor(0x55, 0x66, 0x6E)

_INLINE = re.compile(r"(\*\*.+?\*\*|`[^`]+`|\*[^*]+\*)")


def add_inline(paragraph, text: str, *, base_bold: bool = False) -> None:
    """Write ``text`` into ``paragraph``, honouring bold, italic and code."""
    for part in _INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            run = paragraph.add_run(part[2:-2])
            run.bold = True
        elif part.startswith("`") and part.endswith("`"):
            run = paragraph.add_run(part[1:-1])
            run.font.name = "Consolas"
            run.font.size = Pt(9)
            run.font.color.rgb = ACCENT
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            run = paragraph.add_run(part[1:-1])
            run.italic = True
        else:
            run = paragraph.add_run(part.replace("\\|", "|"))
        if base_bold:
            run.bold = True


def split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def build() -> None:
    document = docx.Document()

    normal = document.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(7)

    for level, size in ((1, 20), (2, 14), (3, 11.5)):
        style = document.styles[f"Heading {level}"]
        style.font.name = "Calibri"
        style.font.size = Pt(size)
        style.font.color.rgb = INK if level == 1 else ACCENT
        style.font.bold = True
        style.paragraph_format.space_before = Pt(16 if level < 3 else 12)
        style.paragraph_format.space_after = Pt(6)

    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if not stripped:
            index += 1
            continue

        if stripped == "---":
            rule = document.add_paragraph()
            rule.paragraph_format.space_before = Pt(2)
            rule.paragraph_format.space_after = Pt(2)
            border = rule.add_run("_" * 78)
            border.font.color.rgb = RGBColor(0xC8, 0xD2, 0xD6)
            border.font.size = Pt(7)
            index += 1
            continue

        heading = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if heading:
            level = min(len(heading.group(1)), 3)
            paragraph = document.add_heading(level=level)
            add_inline(paragraph, heading.group(2))
            index += 1
            continue

        # Pipe table: a header row, a separator row, then body rows.
        if stripped.startswith("|") and index + 1 < len(lines) and set(
            lines[index + 1].strip().replace("|", "").replace(" ", "")
        ) <= {"-", ":"} and lines[index + 1].strip().startswith("|"):
            header = split_row(stripped)
            index += 2
            body: list[list[str]] = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                body.append(split_row(lines[index]))
                index += 1
            table = document.add_table(rows=1, cols=len(header))
            table.style = "Light Grid Accent 1"
            table.alignment = WD_TABLE_ALIGNMENT.LEFT
            for cell, text in zip(table.rows[0].cells, header):
                cell.text = ""
                add_inline(cell.paragraphs[0], text, base_bold=True)
            for row in body:
                cells = table.add_row().cells
                for cell, text in zip(cells, row[: len(header)]):
                    cell.text = ""
                    paragraph = cell.paragraphs[0]
                    add_inline(paragraph, text)
                    if text and text[0].isdigit() or text.startswith("**"):
                        paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            for cell in table.rows[0].cells:
                for run in cell.paragraphs[0].runs:
                    run.font.size = Pt(8.5)
            for row in table.rows[1:]:
                for cell in row.cells:
                    for run in cell.paragraphs[0].runs:
                        run.font.size = Pt(9)
            document.add_paragraph()
            continue

        bullet = re.match(r"^[*-]\s+(.*)$", stripped)
        if bullet:
            paragraph = document.add_paragraph(style="List Bullet")
            add_inline(paragraph, bullet.group(1))
            index += 1
            continue

        numbered = re.match(r"^(\d+)\.\s+(.*)$", stripped)
        if numbered:
            paragraph = document.add_paragraph(style="List Number")
            add_inline(paragraph, numbered.group(2))
            index += 1
            continue

        if stripped.startswith("```"):
            index += 1
            block: list[str] = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                block.append(lines[index])
                index += 1
            index += 1
            paragraph = document.add_paragraph()
            run = paragraph.add_run("\n".join(block))
            run.font.name = "Consolas"
            run.font.size = Pt(9)
            run.font.color.rgb = MUTED
            continue

        paragraph = document.add_paragraph()
        add_inline(paragraph, stripped)
        index += 1

    document.core_properties.title = "PII Redaction — Evaluation Report"
    document.core_properties.author = "Vishal Bansal"
    document.save(str(OUTPUT))


def main() -> int:
    build()
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

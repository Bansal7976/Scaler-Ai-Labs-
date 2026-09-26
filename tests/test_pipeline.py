"""Resolver, surrogate, docx round-trip and end-to-end pipeline tests."""

from __future__ import annotations

import docx
import pytest
from docx.shared import Pt

from piiredact.config import Config
from piiredact.detectors.base import Gazetteer
from piiredact.docio import read_document
from piiredact.docio.docx_writer import Replacement, apply_replacements, clear_metadata
from piiredact.pipeline import build_replacements, detect_spans, redact_document
from piiredact.resolve import ResolutionStats, resolve
from piiredact.surrogates import (
    SurrogateEngine,
    apply_case_style,
    detect_case_style,
    substitute_digits,
)
from piiredact.types import PIIType, Segment, Span


def span(start, end, text, pii_type, detector="t", confidence=1.0) -> Span:
    return Span(start, end, text, pii_type, detector, confidence)


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------
def test_duplicates_merge_and_record_agreement():
    text = "ksh@icicisecurities.com"
    stats = ResolutionStats()
    out = resolve(
        [
            span(0, 23, text, PIIType.EMAIL, "email.regex"),
            span(0, 23, text, PIIType.EMAIL, "gazetteer.sweep"),
        ],
        text,
        stats=stats,
    )
    assert len(out) == 1
    assert stats.merged_duplicates == 1
    assert out[0].detector == "email.regex+gazetteer.sweep"
    assert out[0].evidence["agreeing_detectors"] == ["email.regex", "gazetteer.sweep"]


def test_contained_span_is_absorbed_by_the_longer_one_of_the_same_type():
    text = "Kushal Subbayya Hegde"
    out = resolve(
        [
            span(0, 21, text, PIIType.PERSON),
            span(16, 21, "Hegde", PIIType.PERSON),
        ],
        text,
    )
    assert [s.text for s in out] == ["Kushal Subbayya Hegde"]


def test_higher_priority_type_keeps_its_characters():
    text = "mail ksh@x.com now"
    out = resolve(
        [
            span(5, 15, "ksh@x.com", PIIType.EMAIL),
            span(0, 18, text, PIIType.ORGANIZATION),
        ],
        text,
    )
    kept = {(str(s.pii_type), s.text) for s in out}
    assert ("EMAIL", "ksh@x.com") in kept
    # The organisation claim is truncated around the email, not discarded.
    assert any(t == "ORGANIZATION" for t, _ in kept)


def test_output_is_disjoint_and_sorted():
    text = "a" * 60
    out = resolve(
        [
            span(0, 20, text[:20], PIIType.PERSON),
            span(10, 40, text[10:40], PIIType.ORGANIZATION),
            span(35, 60, text[35:60], PIIType.ADDRESS),
        ],
        text,
    )
    assert out == sorted(out, key=lambda s: s.start)
    for earlier, later in zip(out, out[1:]):
        assert earlier.end <= later.start


def test_confidence_floor_drops_weak_spans():
    text = "Hegde"
    stats = ResolutionStats()
    out = resolve(
        [span(0, 5, text, PIIType.PERSON, confidence=0.4)],
        text,
        min_confidence=0.6,
        stats=stats,
    )
    assert out == []
    assert stats.dropped_low_confidence == 1


# ---------------------------------------------------------------------------
# Surrogates
# ---------------------------------------------------------------------------
@pytest.fixture
def engine() -> SurrogateEngine:
    g = Gazetteer()
    g.add("Kushal Subbayya Hegde", PIIType.PERSON, "t")
    g.add("Kushal Hegde", PIIType.PERSON, "t", canonical="Kushal Subbayya Hegde")
    g.add("Hegde", PIIType.PERSON, "t", canonical="Kushal Subbayya Hegde")
    g.add("KSH International Limited", PIIType.ORGANIZATION, "t")
    g.add("KSH", PIIType.ORGANIZATION, "t", canonical="KSH International Limited")
    g.add("cs.connect@kshinternational.com", PIIType.EMAIL, "t")
    g.add("www.kshinternational.com", PIIType.URL, "t")
    return SurrogateEngine(gazetteer=g, seed="test")


def test_same_entity_always_gets_the_same_surrogate(engine):
    first = engine.surrogate_for(span(0, 21, "Kushal Subbayya Hegde", PIIType.PERSON))
    second = engine.surrogate_for(span(50, 71, "Kushal Subbayya Hegde", PIIType.PERSON))
    assert first == second


def test_surrogates_are_reproducible_across_engines(engine):
    g2 = Gazetteer()
    g2.add("Kushal Subbayya Hegde", PIIType.PERSON, "t")
    other = SurrogateEngine(gazetteer=g2, seed="test")
    assert engine.surrogate_for(
        span(0, 21, "Kushal Subbayya Hegde", PIIType.PERSON)
    ) == other.surrogate_for(span(0, 21, "Kushal Subbayya Hegde", PIIType.PERSON))


def test_a_different_seed_gives_different_values():
    g = Gazetteer()
    g.add("Kushal Subbayya Hegde", PIIType.PERSON, "t")
    a = SurrogateEngine(gazetteer=g, seed="one").surrogate_for(
        span(0, 21, "Kushal Subbayya Hegde", PIIType.PERSON)
    )
    b = SurrogateEngine(gazetteer=g, seed="two").surrogate_for(
        span(0, 21, "Kushal Subbayya Hegde", PIIType.PERSON)
    )
    assert a != b


def test_derived_forms_borrow_the_parents_matching_tokens(engine):
    full = engine.surrogate_for(span(0, 21, "Kushal Subbayya Hegde", PIIType.PERSON))
    short = engine.surrogate_for(span(0, 12, "Kushal Hegde", PIIType.PERSON))
    surname = engine.surrogate_for(span(0, 5, "Hegde", PIIType.PERSON))
    tokens = full.split()
    assert short == f"{tokens[0]} {tokens[-1]}"
    assert surname == tokens[-1]


def test_organisation_keeps_its_legal_suffix_and_acronym_agrees(engine):
    name = engine.surrogate_for(span(0, 25, "KSH International Limited", PIIType.ORGANIZATION))
    acronym = engine.surrogate_for(span(0, 3, "KSH", PIIType.ORGANIZATION))
    assert name.endswith("Limited")
    assert acronym.casefold() == name.split()[0].casefold()


def test_surrogate_never_equals_the_original(engine):
    for text, kind in [
        ("Kushal Subbayya Hegde", PIIType.PERSON),
        ("KSH International Limited", PIIType.ORGANIZATION),
        ("cs.connect@kshinternational.com", PIIType.EMAIL),
    ]:
        assert engine.surrogate_for(span(0, len(text), text, kind)) != text


def test_email_uses_a_reserved_domain_and_keeps_the_role_local_part(engine):
    out = engine.surrogate_for(
        span(0, 31, "cs.connect@kshinternational.com", PIIType.EMAIL)
    )
    assert out.startswith("cs.connect@")
    assert out.endswith((".example", "example.com"))


def test_url_uses_a_reserved_tld_and_agrees_with_the_company(engine):
    org = engine.surrogate_for(span(0, 25, "KSH International Limited", PIIType.ORGANIZATION))
    url = engine.surrogate_for(span(0, 24, "www.kshinternational.com", PIIType.URL))
    assert url.endswith(".example")
    assert org.split()[0].lower() in url.lower()


def test_phone_preserves_country_code_grouping_and_length(engine):
    original = "+91 22 6807 7100"
    out = engine.surrogate_for(span(0, len(original), original, PIIType.PHONE))
    assert out.startswith("+91 ")
    assert len(out) == len(original)
    assert [len(p) for p in out.split()] == [len(p) for p in original.split()]
    assert out != original


def test_case_style_is_preserved(engine):
    upper = engine.surrogate_for(span(0, 21, "KUSHAL SUBBAYYA HEGDE", PIIType.PERSON))
    title = engine.surrogate_for(span(0, 21, "Kushal Subbayya Hegde", PIIType.PERSON))
    assert upper == title.upper()


def test_ip_and_identifier_surrogates_stay_in_safe_ranges(engine):
    ip = engine.surrogate_for(span(0, 11, "203.0.113.9", PIIType.IP_ADDRESS))
    assert ip.startswith(("192.0.2.", "198.51.100.", "203.0.113."))
    din = engine.surrogate_for(span(0, 8, "00135070", PIIType.NATIONAL_ID))
    assert din.isdigit() and len(din) == 8 and din != "00135070"


def test_generated_card_numbers_are_luhn_valid(engine):
    from piiredact.detectors.credit_card import luhn_ok

    out = engine.surrogate_for(
        span(0, 19, "4111 1111 1111 1111", PIIType.CREDIT_CARD)
    )
    assert luhn_ok(out.replace(" ", ""))
    assert len(out) == 19


def test_generated_aadhaar_passes_its_checksum(engine):
    from piiredact.detectors.national_id import aadhaar_ok

    out = engine.surrogate_for(span(0, 14, "2345 6789 0123", PIIType.NATIONAL_ID))
    assert aadhaar_ok(out)


def test_address_surrogate_preserves_shape(engine):
    original = (
        "A29, Abhimanshree Society, Pashan Road, Pune – 411 008, Maharashtra, India"
    )
    out = engine.surrogate_for(span(0, len(original), original, PIIType.ADDRESS))
    assert out.count(",") >= 4
    assert out.rstrip().endswith("India")
    assert "–" in out
    assert "Abhimanshree" not in out and "Pune" not in out


def test_helpers():
    assert detect_case_style("ABC") == "upper"
    assert detect_case_style("Abc Def") == "title"
    assert apply_case_style("abc def", "upper") == "ABC DEF"
    assert substitute_digits("+91 22 6807 7100", "9" * 12) == "+99 99 9999 9999"


# ---------------------------------------------------------------------------
# docx round-trip
# ---------------------------------------------------------------------------
@pytest.fixture
def sample_docx(tmp_path):
    """A small document exercising runs, formatting, tables and merged cells."""
    path = tmp_path / "in.docx"
    document = docx.Document()

    p = document.add_paragraph()
    p.add_run("Contact Person: ")
    bold = p.add_run("Kushal Subbayya Hegde")
    bold.bold = True
    bold.font.size = Pt(14)
    p.add_run(", Managing Director; Telephone: +91 22 6807 7100")

    p2 = document.add_paragraph("E-mail: cs.connect@example-issuer.com")

    table = document.add_table(rows=3, cols=3)
    for index, header in enumerate(("Name", "DIN", "Address")):
        table.cell(0, index).text = header
    table.cell(1, 0).text = "Rajesh Kushal Hegde"
    table.cell(1, 1).text = "00114193"
    table.cell(1, 2).text = (
        "12 Buena Monte, NCL co-operative housing society, Panchvati, Pashan, "
        "Pune – 411 008, Maharashtra, India"
    )
    # A horizontally merged cell, to exercise de-duplication.
    merged = table.cell(2, 0).merge(table.cell(2, 2))
    merged.text = "Kushal Subbayya Hegde"

    document.core_properties.author = "Drafting Lawyer"
    document.save(str(path))
    return path


def test_reader_extracts_structure_and_headers(sample_docx):
    read = read_document(str(sample_docx))
    stats = read.stats()
    assert stats["segments"] > 0 and stats["runs"] > 0
    cells = {
        (h.segment.row_index, h.segment.col_index): h.segment
        for h in read.handles
        if h.segment.is_table_cell
    }
    assert cells[(1, 0)].column_header == "Name"
    assert cells[(1, 1)].column_header == "DIN"
    assert cells[(1, 2)].column_header == "Address"
    # A merged cell is read once, not three times.
    merged_keys = [k for k in cells if k[0] == 2]
    assert len(merged_keys) == 1


def test_writer_preserves_run_formatting(sample_docx, tmp_path):
    read = read_document(str(sample_docx))
    handle = next(h for h in read.handles if "Contact Person" in h.segment.text)
    target = next(s for s in [handle.segment])
    start = target.text.index("Kushal Subbayya Hegde")
    replacement = Replacement(
        span(start, start + 21, "Kushal Subbayya Hegde", PIIType.PERSON), "Samar Sami Barad"
    )
    stats = apply_replacements(handle, [replacement])
    assert stats.replacements_applied == 1

    out = tmp_path / "out.docx"
    read.document.save(str(out))
    written = docx.Document(str(out))
    paragraph = written.paragraphs[0]
    assert "Samar Sami Barad" in paragraph.text
    assert "Kushal Subbayya Hegde" not in paragraph.text
    # The bold, 14pt run survived and now carries the surrogate.
    styled = [r for r in paragraph.runs if r.bold]
    assert styled and "Samar Sami Barad" in "".join(r.text for r in styled)
    assert any(r.font.size == Pt(14) for r in styled)


def test_clear_metadata_blanks_the_author(sample_docx):
    document = docx.Document(str(sample_docx))
    cleared = clear_metadata(document)
    assert cleared.get("author") == "Drafting Lawyer"
    assert document.core_properties.author == ""


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------
def test_end_to_end_redaction(sample_docx, tmp_path):
    out = tmp_path / "redacted.docx"
    result = redact_document(
        sample_docx, out, Config(spacy_model=None), report_dir=tmp_path
    )

    assert out.exists()
    assert result.write_stats.replacements_applied > 0
    assert result.write_stats.replacements_skipped == 0
    assert result.leaks == []

    before = docx.Document(str(sample_docx))
    after = docx.Document(str(out))
    assert len(before.paragraphs) == len(after.paragraphs)
    assert len(before.tables) == len(after.tables)

    text = "\n".join(p.text for p in after.paragraphs)
    for table in after.tables:
        for row in table.rows:
            for cell in row.cells:
                text += "\n" + cell.text

    for original in (
        "Kushal Subbayya Hegde",
        "Rajesh Kushal Hegde",
        "cs.connect@example-issuer.com",
        "+91 22 6807 7100",
        "00114193",
        "Buena Monte",
    ):
        assert original not in text, f"{original!r} survived redaction"

    # Structure words must survive: this is a redaction, not a deletion.
    assert "Contact Person" in text
    assert "Managing Director" in text
    assert "DIN" in text

    for name in ("audit_log.csv", "mapping.csv", "run_summary.json"):
        assert (tmp_path / name).exists()


def test_consistency_across_the_whole_document(sample_docx):
    """The same person in a paragraph and in a table gets the same surrogate."""
    read = read_document(str(sample_docx))
    detection = detect_spans(read.segments, Config(spacy_model=None))
    engine = SurrogateEngine(gazetteer=detection.gazetteer, seed="test")
    plan = build_replacements(detection, engine)
    by_original: dict[str, set[str]] = {}
    for replacements in plan.values():
        for item in replacements:
            by_original.setdefault(item.span.text, set()).add(item.surrogate)
    for original, surrogates in by_original.items():
        assert len(surrogates) == 1, f"{original!r} -> {surrogates}"


def test_disabling_a_detector_removes_only_its_type(sample_docx):
    read = read_document(str(sample_docx))
    with_phone = detect_spans(read.segments, Config(spacy_model=None))
    without = detect_spans(
        read.segments, Config(spacy_model=None).without("phone.pattern")
    )
    assert with_phone.type_counts.get("PHONE", 0) > 0
    assert without.type_counts.get("PHONE", 0) == 0
    assert without.type_counts.get("EMAIL", 0) == with_phone.type_counts.get("EMAIL", 0)


def test_restricting_types_restricts_output(sample_docx):
    read = read_document(str(sample_docx))
    result = detect_spans(
        read.segments, Config(spacy_model=None).with_types([PIIType.EMAIL])
    )
    assert set(result.type_counts) == {"EMAIL"}

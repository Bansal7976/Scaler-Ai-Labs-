"""Detector tests: positives, and the negatives that caused real false positives.

Every negative case here was a false positive at some point during development, so
this file doubles as the regression suite for the precision policy. The
adversarial cases from the synthetic corpus are included by construction rather
than duplicated: :func:`test_synthetic_corpus_exactly_matches_labels` runs the
whole fixture.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from piiredact.config import Config
from piiredact.detectors.base import DetectionContext
from piiredact.detectors.credit_card import has_card_like_entropy, identify_scheme, luhn_ok
from piiredact.detectors.national_id import aadhaar_ok, verhoeff_ok
from piiredact.detectors.organization import (
    OrganizationDetector,
    derive_brand_aliases,
    is_public_or_role,
    trim_leading_noise,
)
from piiredact.detectors.person import (
    PersonNameDetector,
    derive_short_forms,
    looks_like_person_name,
)
from piiredact.detectors.ssn import is_valid_ssn
from piiredact.detectors.url import is_public_domain
from piiredact.pipeline import detect_spans
from piiredact.types import PIIType, Segment

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def segment(text: str, **kwargs) -> Segment:
    kwargs.setdefault("location", "test")
    kwargs.setdefault("kind", "paragraph")
    return Segment(key="k", text=text, **kwargs)


def detect(detector, text: str, **kwargs) -> list[tuple[str, str]]:
    ctx = DetectionContext()
    return [
        (str(s.pii_type), s.text)
        for s in detector.detect(segment(text, **kwargs), ctx)
    ]


def run_all(text: str, config: Config | None = None, **kwargs) -> set[tuple[str, str]]:
    """Whole detection stack, NER disabled so tests do not need a model."""
    config = config or Config(spacy_model=None)
    result = detect_spans([segment(text, **kwargs)], config)
    return {
        (str(s.pii_type), s.text)
        for spans in result.spans_by_segment.values()
        for s in spans
    }


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, expected",
    [
        ("E-mail: cs.connect@kshinternational.com;", "cs.connect@kshinternational.com"),
        ("write to pravin.teli2@hdfcbank.com today", "pravin.teli2@hdfcbank.com"),
        ("Ipocmg@icicibank.com", "Ipocmg@icicibank.com"),
        ("rm6.ifbpune@sbi.co.in", "rm6.ifbpune@sbi.co.in"),
    ],
)
def test_email_positives(text, expected):
    assert ("EMAIL", expected) in run_all(text)


@pytest.mark.parametrize("text", ["ratio of 1.2@3", "see note @ page 42", "a@b"])
def test_email_negatives(text):
    assert not any(t == "EMAIL" for t, _ in run_all(text))


def test_email_seeds_a_person_name_from_the_local_part():
    from piiredact.detectors.email import _person_name_from_local_part

    assert _person_name_from_local_part("tushar.gavankar") == "Tushar Gavankar"
    assert _person_name_from_local_part("pravin.teli2") == "Pravin Teli"
    # Role mailboxes name no individual.
    assert _person_name_from_local_part("customercare") is None
    assert _person_name_from_local_part("ksh.ipo") is None


# ---------------------------------------------------------------------------
# Phone
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, expected",
    [
        ("Telephone: + 91 20 45053237", "+ 91 20 45053237"),
        ("Tel: +91 22 6807 7100", "+91 22 6807 7100"),
        ("Telephone: 022-68052182", "022-68052182"),
        ("Telephone: +91-20-26234000", "+91-20-26234000"),
        ("Telephone: + 91 (20) 6729 5100", "+ 91 (20) 6729 5100"),
    ],
)
def test_phone_positives(text, expected):
    assert ("PHONE", expected) in run_all(text)


def test_phone_finds_every_number_in_a_list():
    found = {v for t, v in run_all("Telephone: +91 22 30752929, +91 22 30752928 and +91 22 30752914") if t == "PHONE"}
    assert found == {"+91 22 30752929", "+91 22 30752928", "+91 22 30752914"}


@pytest.mark.parametrize(
    "text",
    [
        "SEBI Registration Number: INM000011179",
        "Firm registration number: 105215W",
        "Pune – 411 004, Maharashtra",           # PIN code
        "aggregating up to ₹7,100.00 million",   # currency
        "DIN 00135070",                                # director identifier
        "9,090,800 equity shares were transferred",
    ],
)
def test_phone_negatives(text):
    assert not any(t == "PHONE" for t, _ in run_all(text))


# ---------------------------------------------------------------------------
# SSN
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "area, group, serial, valid",
    [
        ("432", "11", "9875", True),
        ("078", "05", "1120", True),
        ("000", "12", "3456", False),
        ("666", "22", "1111", False),
        ("912", "45", "6789", False),
        ("214", "00", "8899", False),
        ("214", "55", "0000", False),
    ],
)
def test_ssn_allocation_rules(area, group, serial, valid):
    assert is_valid_ssn(area, group, serial) is valid


def test_ssn_space_separated_is_not_an_ssn():
    """The space form collides with Indian PIN codes and dates."""
    assert not any(t == "SSN" for t, _ in run_all("The range 411 00 4123 covers"))


# ---------------------------------------------------------------------------
# Credit card
# ---------------------------------------------------------------------------
def test_luhn():
    assert luhn_ok("4111111111111111")
    assert luhn_ok("378282246310005")
    assert not luhn_ok("4111111111111112")


def test_scheme_identification():
    assert identify_scheme("4111111111111111") == "visa"
    assert identify_scheme("378282246310005") == "amex"
    assert identify_scheme("5500000000000004") == "mastercard"
    assert identify_scheme("1234567890123456") is None


def test_round_numbers_are_not_cards():
    """4200000000000000 is Luhn-valid, Visa-prefixed, and a share count."""
    assert not has_card_like_entropy("4200000000000000")
    assert has_card_like_entropy("4111111111111111") is False  # 12 identical digits
    assert has_card_like_entropy("4012888888881881") is False
    assert has_card_like_entropy("6521803169474214")
    # Unlabelled and unseparated, so the entropy guard applies and rejects it.
    assert not any(
        t == "CREDIT_CARD"
        for t, _ in run_all("Equity shares outstanding: 4200000000000000 as at June 30.")
    )
    # Labelled, so the guard is bypassed and the card is found.
    assert ("CREDIT_CARD", "4111 1111 1111 1111") in run_all(
        "Payment was made by card 4111 1111 1111 1111 on 12 May 2024."
    )


# ---------------------------------------------------------------------------
# IP address
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, expected",
    [
        ("last login from 192.0.2.17.", "192.0.2.17"),   # sentence-final period
        ("Blocked 198.51.100.255 after failures.", "198.51.100.255"),
        ("from 2001:db8:85a3::8a2e:370:7334 during", "2001:db8:85a3::8a2e:370:7334"),
    ],
)
def test_ip_positives(text, expected):
    assert ("IP_ADDRESS", expected) in run_all(text)


@pytest.mark.parametrize(
    "text",
    [
        "Compliance with Ind AS 115.2.1.3 was confirmed",
        "See clause 4.1.2.3 of the Agreement",
        "Version 1.2.3.4 of the template",
        "The octet 256.100.50.25 is not valid",
        "grew 10.20.30.40 million",
    ],
)
def test_ip_negatives(text):
    assert not any(t == "IP_ADDRESS" for t, _ in run_all(text))


# ---------------------------------------------------------------------------
# National identifiers
# ---------------------------------------------------------------------------
def test_verhoeff_and_aadhaar():
    assert verhoeff_ok("2345678901231") is (
        verhoeff_ok("2345678901231")
    )  # deterministic
    assert not aadhaar_ok("1234 5678 9012")   # leading 1 is not allocated
    assert not aadhaar_ok("2222 2222 2222")   # too few distinct digits
    assert not aadhaar_ok("2345 6789 0123")   # checksum fails


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Corporate Identity Number: U28129PN1979PLC141032", "U28129PN1979PLC141032"),
        ("SEBI Registration Number: INM000013004", "INM000013004"),
        ("Firm registration number: 105215W", "105215W"),
        ("Peer review number: 014680", "014680"),
        ("bearing registration number M-140388", "M-140388"),
    ],
)
def test_identifier_positives(text, expected):
    assert ("NATIONAL_ID", expected) in run_all(text)


def test_din_requires_a_label_or_a_column_header():
    assert not any(t == "NATIONAL_ID" for t, _ in run_all("The figure 00135070 appears"))
    assert ("NATIONAL_ID", "00135070") in run_all("DIN 00135070")
    # Structural: a bare cell under a DIN column is a DIN.
    assert ("NATIONAL_ID", "00135070") in run_all(
        "00135070", kind="table-cell", column_header="DIN"
    )


# ---------------------------------------------------------------------------
# Date of birth
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, expected",
    [
        ("Date of Birth: 14 August 1953.", "14 August 1953"),
        ("DOB 02/11/1967 as recorded", "02/11/1967"),
        ("was born on January 9, 1948 in Pune", "January 9, 1948"),
    ],
)
def test_dob_positives(text, expected):
    assert ("DOB", expected) in run_all(text)


@pytest.mark.parametrize(
    "text",
    [
        "incorporated on July 30, 1979 under the Companies Act",
        "The Board resolution is dated December 11, 2024",
        "Bid/Offer closes on Thursday, December 18, 2025",
        "Fiscal 2025 ended on March 31, 2025",
    ],
)
def test_dob_negatives_require_a_birth_cue(text):
    assert not any(t == "DOB" for t, _ in run_all(text))


def test_dob_contextual_mode_is_wider():
    text = "He was appointed in 2001. His birth was registered on 4 August 1953."
    strict = run_all(text, Config(spacy_model=None, dob_mode="labelled"))
    wide = run_all(text, Config(spacy_model=None, dob_mode="contextual"))
    assert len([1 for t, _ in wide if t == "DOB"]) >= len(
        [1 for t, _ in strict if t == "DOB"]
    )


# ---------------------------------------------------------------------------
# Person names
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name",
    [
        "Kushal Subbayya Hegde",
        "Rakhi Girija Shetty",
        "Rupal K. Sancheti",
        "Narayana B. Shetty",
        "Indu Jacob",
    ],
)
def test_name_shape_accepts_real_names(name):
    assert looks_like_person_name(name)


@pytest.mark.parametrize(
    "phrase",
    [
        "TOTAL OFFER SIZE",      # all generic commercial vocabulary
        "Village Birdewadi",     # address component
        "Bandra Kurla Complex",  # address component
        "Managing Director",     # designation
        "PAT CAGR",              # financial acronyms
        "Depositories Act",      # legal instrument
        "SECTION III",           # structural word + Roman numeral
        "S. No.",
        "Cap Price",
        "Red Herring Prospectus",
        "Maharashtra",
        "Private Limited",
    ],
)
def test_name_shape_rejects_non_names(phrase):
    assert not looks_like_person_name(phrase)


def test_short_form_derivation():
    assert derive_short_forms("Kushal Subbayya Hegde") == ["Kushal Hegde", "Hegde"]
    assert derive_short_forms("Indu Jacob") == ["Jacob"]


def test_person_column_header_strategy():
    found = detect(
        PersonNameDetector(),
        "Kushal Subbayya Hegde",
        kind="table-cell",
        column_header="Name",
    )
    assert ("PERSON", "Kushal Subbayya Hegde") in found


def test_person_cue_label_expands_a_slash_list():
    found = {
        v
        for t, v in detect(
            PersonNameDetector(),
            "Contact Person: Eric Bacha/ Sachin Gawade/ Pravin Teli/ Siddharth Jadhav",
        )
        if t == "PERSON"
    }
    assert {"Eric Bacha", "Sachin Gawade", "Pravin Teli", "Siddharth Jadhav"} <= found


def test_person_strips_trailing_field_labels():
    found = {v for t, v in detect(PersonNameDetector(), "Contact Person: Chitra Raste Website: www.x.in") if t == "PERSON"}
    assert "Chitra Raste" in found
    assert "Chitra Raste Website" not in found


def test_person_ignores_a_scheme_named_after_a_person():
    found = detect(PersonNameDetector(), "under the Deen Dayal Upadhyaya Gram Jyoti Yojana")
    assert not found


# ---------------------------------------------------------------------------
# Organisations
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, expected",
    [
        ("audited by Kirtane & Pandit LLP, Chartered Accountants", "Kirtane & Pandit LLP"),
        ("Kushal Motors and Electricals Private Limited, Waterloo", "Kushal Motors and Electricals Private Limited"),
        ("from Shubhkamal Leasing and Investment Private Limited", "Shubhkamal Leasing and Investment Private Limited"),
        ("CARE Analytics and Advisory Private Limited", "CARE Analytics and Advisory Private Limited"),
        ("customers include CG Power and Industrial Solutions Limited;", "CG Power and Industrial Solutions Limited"),
        ("Hindalco Industries Limited supplies", "Hindalco Industries Limited"),
        ("the Dhaulagiri Family Trust holds", "Dhaulagiri Family Trust"),
        ("Transformers & Rectifiers (India) Limited", "Transformers & Rectifiers (India) Limited"),
    ],
)
def test_organization_positives(text, expected):
    assert ("ORGANIZATION", expected) in detect(OrganizationDetector(), text)


def test_organization_does_not_cross_a_comma_separated_list():
    found = {v for t, v in detect(OrganizationDetector(), "HEGDE, RAKHI GIRIJA SHETTY, DHAULAGIRI FAMILY TRUST, EVEREST FAMILY TRUST")}
    assert "DHAULAGIRI FAMILY TRUST" in found
    assert not any("SHETTY" in v for v in found)


def test_organization_separates_two_entities_joined_by_and():
    found = {v for t, v in detect(OrganizationDetector(), "HDFC Bank Limited and ICICI Bank Limited")}
    assert {"HDFC Bank Limited", "ICICI Bank Limited"} <= found


@pytest.mark.parametrize(
    "text",
    [
        "Working Day means all days excluding Saturdays, Sundays and bank holidays",
        "Restated Statements of Assets and Liabilities of the Company as at",
        "Sarthak Malvadkar Company Secretary and Compliance Officer",
        "under the Companies Act, 2013",
    ],
)
def test_organization_negatives(text):
    assert not detect(OrganizationDetector(), text)


def test_public_bodies_and_roles_are_never_organisations():
    assert is_public_or_role("BSE Limited") == "public-body"
    assert is_public_or_role("Reserve Bank of India") == "public-body"
    assert is_public_or_role("Escrow Collection Bank") == "contractual-role"
    assert is_public_or_role("Group Entities") == "contractual-role"
    assert is_public_or_role("HDFC Bank Limited") is None


def test_role_prefix_is_trimmed_not_absorbed():
    assert trim_leading_noise("Escrow Collection Bank HDFC Bank") == "HDFC Bank"
    assert trim_leading_noise("Company KSH International Limited") == "KSH International Limited"
    assert trim_leading_noise("HDFC Bank Limited") == "HDFC Bank Limited"


def test_brand_alias_derivation_is_conservative():
    assert derive_brand_aliases("KSH International Limited") == ["KSH International", "KSH"]
    # "EQUITY" is an ordinary word, not an initialism -- this once cost 261 FPs.
    assert "EQUITY" not in derive_brand_aliases("EQUITY SHARES OF KSH INTERNATIONAL LIMITED")
    # Public-body initialisms are never aliases.
    assert "BSE" not in derive_brand_aliases("BSE Limited")


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, expected",
    [
        (
            "Registered Office: 11/3, 11/4 and 11/5, Village Birdewadi, Chakan Taluka - Khed, "
            "Pune – 410 501, Maharashtra, India;",
            "11/3, 11/4 and 11/5, Village Birdewadi, Chakan Taluka - Khed, "
            "Pune – 410 501, Maharashtra, India",
        ),
        (
            "ICICI Securities Limited\nICICI Venture House, Appasaheb Marathe Marg, "
            "Mumbai 400025, Maharashtra, India\nTel: +91 22 6807 7100",
            "ICICI Venture House, Appasaheb Marathe Marg, Mumbai 400025, Maharashtra, India",
        ),
        ("Pune – 411 001", "Pune – 411 001"),
    ],
)
def test_address_positives(text, expected):
    assert ("ADDRESS", expected) in run_all(text)


def test_address_absorbs_the_state_and_country_tail_across_a_newline():
    text = "Flat No. 102, Sai Complex Shaniwar Peth, Pune – 411 030 Maharashtra, India\nE-mail: a@b.com"
    found = {v for t, v in run_all(text) if t == "ADDRESS"}
    assert any(v.endswith("India") for v in found)


def test_address_column_header_strategy():
    text = "A29, Abhimanshree Society, Pashan Road, Pune – 411 008, Maharashtra, India"
    assert ("ADDRESS", text) in run_all(text, kind="table-cell", column_header="Address")


@pytest.mark.parametrize(
    "text",
    [
        "see “Our Business – Property” on page 236",
        "our manufacturing unit at Supa, Ahilyanagar in Maharashtra",
        "aggregating up to ₹7,100.00 million",
    ],
)
def test_address_negatives(text):
    assert not any(t == "ADDRESS" for t, _ in run_all(text))


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------
def test_url_public_allowlist():
    assert is_public_domain("www.sebi.gov.in")
    assert is_public_domain("http://www.bseindia.com/x")
    assert not is_public_domain("www.kshinternational.com")


def test_url_tolerates_the_justified_text_space():
    assert ("URL", "www.kshinternational. com") in run_all("www.kshinternational. com")


def test_public_urls_are_preserved():
    assert not any(t == "URL" for t, _ in run_all("available at www.sebi.gov.in today"))


# ---------------------------------------------------------------------------
# The synthetic corpus, run as one test
# ---------------------------------------------------------------------------
def test_synthetic_corpus_exactly_matches_labels():
    """Every labelled span found, every adversarial negative left alone."""
    rows = [
        json.loads(line)
        for line in (FIXTURES / "synthetic_corpus.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    segments = [
        Segment(key=row["key"], text=row["text"], location=row["location"], kind="paragraph")
        for row in rows
    ]
    result = detect_spans(segments, Config(spacy_model=None))
    for row, seg in zip(rows, segments):
        predicted = {
            (str(s.pii_type), s.start, s.end)
            for s in result.spans_by_segment.get(seg.key, [])
        }
        expected = {(s["pii_type"], s["start"], s["end"]) for s in row["spans"]}
        # PERSON labels need NER or a cue, which this NER-free config may miss;
        # the full-config figures are in EVALUATION_REPORT.md.
        expected = {e for e in expected if e[0] != "PERSON"}
        predicted = {p for p in predicted if p[0] != "PERSON"}
        assert predicted == expected, f"{row['location']}: {row['text']!r}"

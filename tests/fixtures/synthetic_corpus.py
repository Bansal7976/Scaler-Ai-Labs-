"""Synthetic corpus for the PII types that do not occur in the KSH prospectus.

SSN, credit card, date of birth and IP address are all in the assignment's
minimum set and none of them appears in an Indian IPO prospectus. Measuring them
on that document would report precision and recall of 0/0, which says nothing
about whether the detectors work.

So this fixture supplies programmatically-labelled text for those four types,
plus adversarial negatives drawn from the shapes that actually cause false
positives in financial prose: nine-digit amounts, sixteen-digit reference
numbers, dotted clause numbers and the document's many non-birth dates.

Because the labels come from the generator rather than from a human reading the
tool's output, this half of the evaluation is free of the annotator/author
circularity that the KSH gold standard cannot fully escape.

Running this module writes ``synthetic_corpus.jsonl``.
"""

from __future__ import annotations

import json
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent / "synthetic_corpus.jsonl"

#: ``(text, [(pii_type, substring)])`` -- substrings are resolved to offsets.
CASES: list[tuple[str, list[tuple[str, str]]]] = [
    # ---------------- SSN: positives -------------------------------------
    ("Employee record for J. Carter, SSN 432-11-9875, hired March 2019.",
     [("PERSON", "J. Carter"), ("SSN", "432-11-9875")]),
    ("Social Security Number: 078-05-1120 was verified against the W-2.",
     [("SSN", "078-05-1120")]),
    ("The beneficiary's SSN is 219456789 per the 1099 filed last year.",
     [("SSN", "219456789")]),
    ("Two claimants: 001-01-0001 and 665-99-9999 remain unresolved.",
     [("SSN", "001-01-0001"), ("SSN", "665-99-9999")]),
    # ---------------- SSN: negatives (must not fire) ----------------------
    ("Invoice reference 000-12-3456 was cancelled.", []),          # area 000
    ("Docket 666-22-1111 was dismissed without costs.", []),       # area 666
    ("Tax ID 912-45-6789 belongs to a non-resident entity.", []),  # area 9xx
    ("Lot 214-00-8899 failed inspection.", []),                    # group 00
    ("Batch 214-55-0000 was destroyed.", []),                      # serial 0000
    ("The PIN code range 411 004 123 covers three localities.", []),
    ("Aggregate consideration was 432119875 rupees in Fiscal 2024.", []),

    # ---------------- Credit card: positives ------------------------------
    ("Payment was made by card 4111 1111 1111 1111 on 12 May 2024.",
     [("CREDIT_CARD", "4111 1111 1111 1111")]),
    ("Card number: 5500-0000-0000-0004 (Mastercard), expiry 09/27.",
     [("CREDIT_CARD", "5500-0000-0000-0004")]),
    ("American Express 378282246310005 was declined twice.",
     [("CREDIT_CARD", "378282246310005")]),
    ("Discover 6011111111111117 and Visa 4012888888881881 were both refunded.",
     [("CREDIT_CARD", "6011111111111117"), ("CREDIT_CARD", "4012888888881881")]),
    ("The cardholder's RuPay 6521 8031 6947 4214 is on file.",
     [("CREDIT_CARD", "6521 8031 6947 4214")]),
    # ---------------- Credit card: negatives ------------------------------
    ("Order number 4111 1111 1111 1112 was shipped.", []),          # Luhn fails
    ("Reference 1234 5678 9012 3456 relates to the escrow account.", []),
    ("Equity shares outstanding: 4200000000000000 as at June 30.", []),
    ("Ticket 9876543210987654 was closed by the support desk.", []),

    # ---------------- Date of birth: positives ---------------------------
    ("Date of Birth: 14 August 1953. Appointed to the board in 2001.",
     [("DOB", "14 August 1953")]),
    ("DOB 02/11/1967 as recorded in the passport application.",
     [("DOB", "02/11/1967")]),
    ("The director was born on January 9, 1948 in Pune.",
     [("DOB", "January 9, 1948")]),
    ("Date of birth 1971-04-30 per the PAN database.",
     [("DOB", "1971-04-30")]),
    # ---------------- Date of birth: negatives ---------------------------
    ("Our Company was incorporated on July 30, 1979 under the Companies Act.", []),
    ("The Board resolution is dated December 11, 2024.", []),
    ("Bid/Offer closes on Thursday, December 18, 2025.", []),
    ("Fiscal 2025 ended on March 31, 2025 with revenue of 12,400.20 million.", []),
    ("A certificate dated 04/07/1996 was issued by the Registrar of Companies.", []),

    # ---------------- IP address: positives ------------------------------
    ("The upload originated from 203.0.113.42 at 04:12 UTC.",
     [("IP_ADDRESS", "203.0.113.42")]),
    ("Servers 10.20.30.40 and 172.16.254.1 were both unreachable.",
     [("IP_ADDRESS", "10.20.30.40"), ("IP_ADDRESS", "172.16.254.1")]),
    ("Access logged from 2001:db8:85a3::8a2e:370:7334 during the audit.",
     [("IP_ADDRESS", "2001:db8:85a3::8a2e:370:7334")]),
    ("Blocked 198.51.100.255 after repeated failures.",
     [("IP_ADDRESS", "198.51.100.255")]),
    # ---------------- IP address: negatives ------------------------------
    ("Compliance with Ind AS 115.2.1.3 was confirmed by the auditors.", []),
    ("See clause 4.1.2.3 of the Underwriting Agreement.", []),
    ("Reported under paragraph 12.4.1.7 of the accounting policy.", []),
    ("Version 1.2.3.4 of the reporting template was superseded.", []),
    ("The octet 256.100.50.25 is not a valid address.", []),
    ("Total borrowings of 10.20 million and 30.40 million respectively.", []),

    # ---------------- Mixed records, to test interaction ------------------
    ("Claimant: Maria Gonzalez, DOB 07/22/1980, SSN 512-88-7431, "
     "card 4242 4242 4242 4242, last login from 192.0.2.17.",
     [("DOB", "07/22/1980"), ("SSN", "512-88-7431"),
      ("CREDIT_CARD", "4242 4242 4242 4242"), ("IP_ADDRESS", "192.0.2.17"),
      ("PERSON", "Maria Gonzalez")]),
    ("Contact Person: David Okonkwo, Date of Birth: 3 March 1975, "
     "email david.okonkwo@example.org, telephone +1 415 555 0132.",
     [("PERSON", "David Okonkwo"), ("DOB", "3 March 1975"),
      ("EMAIL", "david.okonkwo@example.org"), ("PHONE", "+1 415 555 0132")]),
]


def build() -> list[dict]:
    rows: list[dict] = []
    for index, (text, labels) in enumerate(CASES):
        spans = []
        for pii_type, substring in labels:
            start = text.find(substring)
            if start < 0:
                raise ValueError(f"case {index}: {substring!r} not in text")
            spans.append(
                {
                    "pii_type": pii_type,
                    "start": start,
                    "end": start + len(substring),
                    "text": substring,
                }
            )
        spans.sort(key=lambda s: s["start"])
        rows.append(
            {
                "index": index,
                "key": f"synthetic/{index}",
                "stratum": "synthetic",
                "location": f"synthetic case {index}",
                "text": text,
                "spans": spans,
            }
        )
    return rows


def main() -> int:
    rows = build()
    with OUTPUT.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    positives = sum(len(row["spans"]) for row in rows)
    negatives = sum(1 for row in rows if not row["spans"])
    print(f"wrote {OUTPUT}")
    print(f"  {len(rows)} cases, {positives} labelled spans, {negatives} pure negatives")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

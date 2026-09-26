"""The hand-written gold standard for the evaluation sample.

Annotations are ``(type, exact_substring, occurrence)`` per segment, keyed by the
segment's index in ``sample.json``. They were written by reading each segment's
raw text against ``annotation_guideline.md``, which was frozen first.

Running this module resolves every annotation to character offsets against the
live sample and writes ``gold_standard.jsonl``. It **fails loudly** if a
substring is absent or if the requested occurrence does not exist, so a typo
here can never become a silent false negative in the metrics.

Segments not listed below are asserted to contain no PII at all. That is the
point of the ``negative`` and ``prose`` strata: their emptiness is an annotation
too, and it is what the precision figures are measured against.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample.json"
OUTPUT = HERE / "gold_standard.jsonl"

EN_DASH = "–"

# Addresses that recur verbatim; named to keep the table below readable and to
# make a transcription error impossible to introduce twice.
REG_OFFICE = (
    "11/3, 11/4 and 11/5, Village Birdewadi, Chakan Taluka - Khed, "
    f"Pune {EN_DASH} 410 501, Maharashtra, India"
)
REG_OFFICE_NOHYPH = (
    "11/3, 11/4 and 11/5, Village Birdewadi, Chakan Taluka-Khed, "
    f"Pune {EN_DASH} 410 501, Maharashtra, India"
)
CORP_OFFICE = (
    "201, Tower 2, Montreal Business Centre, Off Pallod Farms, Baner, "
    f"Pune {EN_DASH} 411 045, Maharashtra, India"
)
ICICI_ADDRESS_PARA = (
    "ICICI Venture House Appasaheb Marathe Marg Prabhadevi, "
    f"Mumbai {EN_DASH} 400 025 Maharashtra, India"
)
HDFC_ADDRESS = (
    "Next to Kanjurmarg Railway Station, Kanjurmarg (East) "
    f"Mumbai {EN_DASH} 400042, Maharashtra, India"
)
ICICI_BANK_ADDRESS = (
    "163, 5th Floor, H.T.Parekh Marg Backbay Reclamation Churchgate, "
    f"Mumbai {EN_DASH} 400020"
)
HDFC_PHONES = ["+91 22 30752929", "+91 22 30752928", "+91 22 30752914"]
HDFC_EMAILS = [
    "siddharth.jadhav@hdfcbank.com",
    "sachin.gawade@hdfcbank.com",
    "eric.bacha@hdfcbank.com",
    "tushar.gavankar@hdfcbank.com",
    "pravin.teli2@hdfcbank.com",
]
HDFC_CONTACTS = [
    "Eric Bacha",
    "Sachin Gawade",
    "Pravin Teli",
    "Siddharth Jadhav",
    "Tushar Gavankar",
]
PROMOTER_PEOPLE_UPPER = [
    "KUSHAL SUBBAYYA HEGDE",
    "PUSHPA KUSHAL HEGDE",
    "RAJESH KUSHAL HEGDE",
    "ROHIT KUSHAL HEGDE",
    "RAKHI GIRIJA SHETTY",
]
PROMOTER_PEOPLE = [
    "Kushal Subbayya Hegde",
    "Pushpa Kushal Hegde",
    "Rajesh Kushal Hegde",
    "Rohit Kushal Hegde",
    "Rakhi Girija Shetty",
]


def _p(*names: str) -> list[tuple[str, str, int]]:
    return [("PERSON", n, 0) for n in names]


def _o(*names: str) -> list[tuple[str, str, int]]:
    return [("ORGANIZATION", n, 0) for n in names]


def _a(*values: str) -> list[tuple[str, str, int]]:
    return [("ADDRESS", v, 0) for v in values]


def _e(*values: str) -> list[tuple[str, str, int]]:
    return [("EMAIL", v, 0) for v in values]


def _ph(*values: str) -> list[tuple[str, str, int]]:
    return [("PHONE", v, 0) for v in values]


def _u(*values: str) -> list[tuple[str, str, int]]:
    return [("URL", v, 0) for v in values]


def _id(*values: str) -> list[tuple[str, str, int]]:
    return [("NATIONAL_ID", v, 0) for v in values]


#: segment index in sample.json -> annotations. Absent index == no PII.
GOLD: dict[str, list[tuple[str, str, int]]] = {
    # --- cover page table -------------------------------------------------
    "body/tbl0/r1c0": _a(
        "11/3, 11/4 and 11/5 Village Birdewadi Chakan Taluka - Khed "
        f"Pune {EN_DASH} 410 501\nMaharashtra, India"
    ),
    "body/tbl0/r1c1": _a(
        "201, Tower 2, Montreal Business Centre, Off Pallod Farms, Baner "
        f"Pune {EN_DASH} 411 045\nMaharashtra, India"
    ),
    "body/tbl0/r1c2": _p("Sarthak Malvadkar"),
    "body/tbl0/r1c4": _e("cs.connect@kshinternational.com") + _ph("+ 91 20 45053237"),
    "body/tbl0/r1c6": _u("www.kshinternational. com"),
    "body/tbl0/r2c0": _p(*PROMOTER_PEOPLE_UPPER)
    + _o(
        "DHAULAGIRI FAMILY TRUST",
        "EVEREST FAMILY TRUST",
        "MAKALU FAMILY TRUST",
        "BROAD FAMILY TRUST",
        "ANNAPURNA FAMILY TRUST",
        "KANCHENJUNGA FAMILY TRUST",
        "WATERLOO INDUSTRIAL PARK VI PRIVATE LIMITED",
    ),
    "body/tbl0/r8c0": _p("Kushal Subbayya Hegde"),
    "body/tbl0/r9c0": _p("Pushpa Kushal Hegde"),
    "body/tbl0/r10c0": _p("Rajesh Kushal Hegde"),
    "body/tbl0/r11c0": _p("Rohit Kushal Hegde"),
    "body/tbl0/r12c0": _o("Kirtane & Pandit LLP"),
    # --- SEBI disclaimer table (BRLM contact columns) ---------------------
    "body/tbl1/r7c7": _e("ksh.ipo@nuvama.com") + _ph("+91 22 4009 4400"),
    "body/tbl1/r8c7": _e("ksh@icicisecurities.com") + _ph("+91 22 6807 7100"),
    "body/tbl1/r11c3": _ph("+91 81081 14949") + _e("kshinternational.ipo@in.mpms.mufg.com"),
    # --- cover page prose -------------------------------------------------
    "body/p23": _o("KSH INTERNATIONAL LIMITED"),
    "body/p24": [
        ("ORGANIZATION", "Bhandary Metal Extrusion Private Limited", 0),
        ("ORGANIZATION", "Bhandary Metal Extrusion Private Limited", 1),
        ("ORGANIZATION", "KSH International Private Limited", 0),
        ("ORGANIZATION", "KSH International Limited", 0),
    ],
    "body/p25": _id("U28129PN1979PLC141032"),
    "body/p26": _a(REG_OFFICE),
    "body/p27": _a(CORP_OFFICE),
    "body/p28": _p("Sarthak Malvadkar") + _ph("+ 91 20 4505 3237"),
    "body/p29": _e("cs.connect@kshinternational.com") + _u("www.kshinternational.com"),
    "body/tbl2/r0c0": _p(*PROMOTER_PEOPLE_UPPER)
    + _o(
        "DHAULAGIRI FAMILY TRUST",
        "EVEREST\nFAMILY TRUST",
        "MAKALU FAMILY TRUST",
        "BROAD FAMILY TRUST",
        "ANNAPURNA FAMILY TRUST",
        "KANCHENJUNGA FAMILY TRUST",
        "WATERLOO INDUSTRIAL PARK VI PRIVATE LIMITED",
    ),
    "body/tbl2/r1c0": _o("KSH INTERNATIONAL LIMITED") + _p(*PROMOTER_PEOPLE_UPPER[:4]),
    # --- General Information: BRLMs and registrar -------------------------
    "body/tbl2/r14c0": _o("Nuvama Wealth Management Limited")
    + _a(
        "801 - 804, Wing A, Building No 3, Inspire BKC, G Block, "
        "Bandra Kurla Complex, Bandra East, Mumbai 400051, Maharashtra, India"
    )
    + _ph("+91 22 40094400")
    + _e("ksh.ipo@nuvama.com", "customerservice.mb@nuvama.com")
    + _u("www.nuvama.com")
    + _p("Lokesh Shah", "Soumavo Sarkar")
    + _id("INM000013004"),
    "body/tbl2/r14c2": _o("ICICI Securities Limited")
    + _a(
        "ICICI Venture House, Appasaheb Marathe Marg, Prabhadevi, "
        "Mumbai 400025, Maharashtra, India"
    )
    + _ph("+91 22 6807 7100")
    + _e("ksh@icicisecurities.com", "customercare@icicisecurities.com")
    + _u("www.icicisecurities.com")
    + _p("Kishan Rastogi", "Abhijit Diwan")
    + _id("INM000011179"),
    "body/tbl2/r14c6": _o("MUFG Intime India Private Limited", "Link Intime India Private Limited")
    + _a(
        "C-101, Embassy 247, 1st Floor, L B S Marg, Vikhroli (West), "
        "Mumbai 400083, (Maharashtra), India"
    )
    + _ph("+91 81081 14949")
    + [
        ("EMAIL", "kshinternational.ipo@in.mpms.mufg.com", 0),
        ("EMAIL", "kshinternational.ipo@in.mpms.mufg.com", 1),
    ]
    + _u("www.in.mpms.mufg.com")
    + _p("Shanti Gopalkrishnan")
    + _id("INR000004058"),
    # --- definitions ------------------------------------------------------
    "body/p48": _o("KSH International Limited") + _a(REG_OFFICE, CORP_OFFICE),
    "body/tbl3/r1c1": _o("KSH International Limited") + _a(REG_OFFICE),
    "body/tbl5/r1c1": _a(CORP_OFFICE),
    "body/tbl5/r12c1": _p("Lalit Muljibhai Sarvaiya") + _id("M-140388"),
    "body/tbl5/r29c1": _a(REG_OFFICE_NOHYPH),
    "body/tbl6/r11c1": _a(
        "Plot No. J-25, Taloja Industrial Area, Village Padghe, Taluka Panvel, "
        f"Raigad {EN_DASH} 410 208, Maharashtra, India"
    ),
    "body/tbl6/r12c1": _a(REG_OFFICE),
    "body/tbl6/r13c1": _a(
        "Plot No. 5, Chakan Industrial Area, Phase II, Village Khalumbre, "
        f"Taluka Khed, Pune {EN_DASH} 410 501, Maharashtra, India"
    ),
    # --- offer-related definitions and capital structure -------------------
    "body/tbl11/r6c1": _p(*PROMOTER_PEOPLE[:4]),
    "body/tbl23/r3c1": _p("Kushal Subbayya Hegde"),
    "body/tbl31/r2c1": _o("Waterloo Motors"),
    "body/tbl37/r6c1": _p("Pushpa Kushal Hegde"),
    "body/tbl53/r16c3": _p(
        "Kushal Hegde", "Rajesh Hegde", "Rohit Hegde", "Pushpa Hegde"
    ),
    "body/tbl23/r10c1": _o("Makalu Family Trust"),
    "body/tbl25/r11c0": _o("Annapurna Family Trust"),
    "body/p276": _o("Kirtane & Pandit LLP"),
    "body/tbl37/r5c1": _p("Pushpa Kushal Hegde"),
    # --- risk factors / business ------------------------------------------
    "body/p443": _p("Rohit Kushal Hegde"),
    "body/p526": _a(
        "Plot No. F-223, Supa Parner Industrial Park, Mauje Palve Khurd, "
        f"Taluka Parner, Dist {EN_DASH} Ahmednagar, Maharashtra {EN_DASH} 414 301"
    ),
    "body/p617": _p(*PROMOTER_PEOPLE),
    # --- General Information: offices and board ---------------------------
    "body/p718": _o("KSH International Limited"),
    "body/p720": _a(f"Pune {EN_DASH} 410 501"),
    "body/p723": _o("KSH International Limited"),
    "body/p732": _a(
        "PCNTDA Green Building Block A 1st and 2nd floor Near Akurdi Railway "
        f"Station Akurdi, Pune {EN_DASH} 411 044 Maharashtra, India"
    ),
    "body/tbl70/r1c0": _p("Kushal Subbayya Hegde"),
    "body/tbl70/r1c2": _id("00135070"),
    "body/tbl70/r1c3": _a(
        "S. no. 245/ 104, Pushpakamal, Deccan Gymkhana Society, lane no. 3 "
        "Prabhat Road, opposite PYC basketball court, Deccan Gymkhana, "
        f"Pune {EN_DASH} 411 004 Maharashtra, India"
    ),
    "body/tbl70/r2c0": _p("Rajesh Kushal Hegde"),
    "body/tbl70/r2c2": _id("00114193"),
    "body/tbl70/r2c3": _a(
        "12 Buena Monte, NCL co-operative housing society, Panchvati, Pashan, "
        f"Pune {EN_DASH} 411 008, Maharashtra, India"
    ),
    "body/tbl70/r3c0": _p("Rohit Kushal Hegde"),
    "body/tbl70/r3c2": _id("00134926"),
    "body/tbl70/r3c3": _a(
        f"Pushpakamal Apartment, Flat {EN_DASH} 1, S. no. 245/ 104, Prabhat Road "
        f"Lane no. 3, Shivaji Nagar, Deccan Gymkhana, Pune {EN_DASH} 411 004, "
        "Maharashtra, India"
    ),
    "body/tbl70/r4c0": _p("Rakhi Girija Shetty"),
    "body/tbl70/r4c2": _id("03124510"),
    "body/tbl70/r4c3": _a(
        "S. no. 245/ 104, Pushpakamal, Deccan Gymkhana Society, lane no.\n3 "
        "Prabhat Road, opposite PYC basketball court, Erandawane, "
        f"Deccan Gymkhana, Pune {EN_DASH} 411 004 Maharashtra, India"
    ),
    "body/tbl70/r5c0": _p("Dinesh Hirachand Munot"),
    "body/tbl70/r5c2": _id("00049801"),
    "body/tbl70/r5c3": _a(
        "Pratik Bunglow, Senapati Bapat Road, behind Sahara Hotel, Shivajinagar, "
        f"Model Colony, Pune {EN_DASH} 411 016, Maharashtra, India"
    ),
    "body/tbl70/r6c0": _p("Ajay Shriram Patil"),
    "body/tbl70/r6c2": _id("01217000"),
    "body/tbl70/r6c3": _a(
        "602, Gopalkrupa Apartment, Bhonde colony, Prabhat Road, Erandawane, "
        f"Pune {EN_DASH} 411 004, Maharashtra, India"
    ),
    "body/tbl70/r7c0": _p("Ram Kumar Tiwari"),
    "body/tbl70/r7c2": _id("10938958"),
    "body/tbl70/r7c3": _a(
        "A-259, JK Road, Minal Residency, Huzur, Govindpura, "
        f"Bhopal {EN_DASH} 462 023, Madhya Pradesh, India"
    ),
    "body/tbl70/r8c0": _p("Indu Jacob"),
    "body/tbl70/r8c2": _id("05293084"),
    "body/tbl70/r8c3": _a(
        "A29, Abhimanshree Society, Pashan Road, "
        f"Pune {EN_DASH} 411 008, Maharashtra, India"
    ),
    "body/p742": _a(f"Taluka Khed, District Pune {EN_DASH} 410 501"),
    "body/p744": _ph("+ 91 20 45053237"),
    "body/p745": _e("Sarthak.malvadkar@kshinterantional.com"),
    "body/p755": _a(f"Bandra East, Mumbai {EN_DASH} 400 051 Maharashtra, India"),
    "body/p756": _ph("+91 22 40094400") + _e("ksh.ipo@nuvama.com") + _u("www.nuvama.com"),
    "body/p757": _e("customerservice.mb@nuvama.com"),
    "body/p761": _o("ICICI Securities Limited") + _a(ICICI_ADDRESS_PARA),
    "body/p762": _ph("+91 22 6807 7100")
    + _e("ksh@icicisecurities.com", "customercare@icicisecurities.com")
    + _u("www.icicisecurities.com"),
    "body/p779": _ph("+ 91 22 4009 4400"),
    "body/p780": _e(
        "ksh.ipo@nuvama.com",
        "prakash.boricha@nuvama.com",
        "sheetal.parab@nuvama.com",
    ),
    "body/p782": _p("Prakash Boricha"),
    "body/p785": _o("ICICI Securities Limited") + _a(ICICI_ADDRESS_PARA),
    "body/p786": _ph("+91 22 6807 7100")
    + _e("ksh@icicisecurities.com")
    + _u("www.icicisecurities.com"),
    "body/p787": _e("customercare@icicisecurities.com"),
    "body/p794": _a(f"Senapati Bapat Marg, Lower Parel (West) Mumbai {EN_DASH} 400 013"),
    "body/p796": _e("ipo@trilegal.com"),
    "body/p797": _ph("+91 22 4079 1000"),
    "body/p801": _a("1st Floor, L B S Marg, Vikhroli (West) Mumbai 400083, (Maharashtra), India")
    + _ph("+91 81081 14949"),
    "body/p802": _e("kshinternational.ipo@in.mpms.mufg.com"),
    "body/p803": _e("kshinternational.ipo@in.mpms.mufg.com"),
    "body/p804": _u("www.in.mpms.mufg.com") + _p("Shanti Gopalkrishnan") + _id("INR000004058"),
    "body/p809": _a(HDFC_ADDRESS),
    "body/p810": _ph(*HDFC_PHONES),
    "body/p811": _e(*HDFC_EMAILS) + _u("www.hdfcbank.com"),
    "body/p812": _p(*HDFC_CONTACTS),
    "body/p819": _a(ICICI_BANK_ADDRESS)
    + _ph("022-68052182")
    + _e("Ipocmg@icicibank.com")
    + _u("www.icicibank.com")
    + _p("Varun Badai"),
    "body/p825": _a(HDFC_ADDRESS),
    "body/p826": _ph(*HDFC_PHONES),
    "body/p827": _e(*HDFC_EMAILS) + _u("www.hdfcbank.com"),
    "body/p828": _p(*HDFC_CONTACTS),
    "body/p835": _a(ICICI_BANK_ADDRESS)
    + _ph("022-68052182")
    + _e("Ipocmg@icicibank.com")
    + _u("www.icicibank.com")
    + _p("Varun Badai"),
    "body/p840": _a(HDFC_ADDRESS),
    "body/p841": _ph(*HDFC_PHONES),
    "body/p842": _e(*HDFC_EMAILS) + _u("www.hdfcbank.com"),
    "body/p843": _p(*HDFC_CONTACTS),
    "body/p871": _a(f"Pune {EN_DASH} 411 038"),
    "body/p873": _e("parag.pansare@kirtanepandit.com"),
    "body/p874": _ph("+ 91 (20) 6729 5100"),
    "body/tbl73/r1c0": _o("Kirtane & Pandit, LLP")
    + _a(
        "5th Floor, Gopal House\nOpposite Harshal Hall, above HDFC Limited "
        f"Karve Road, Pune {EN_DASH} 411 038\nMaharashtra, India"
    )
    + _e("parag.pansare@kirtanepandit.com")
    + _ph("+ 91 20 6729 5100")
    + _id("105215W", "W100057", "014680"),
    "body/tbl73/r2c0": _o("Hingne Tare & Associates")
    + _a(
        "Flat No. 102, Sai Complex Shaniwar Peth, "
        f"Pune {EN_DASH} 411 030 Maharashtra, India"
    )
    + _e("hingnetare@gmail.com")
    + _id("116417W"),
    "body/p886": _a(f"Koregaon Park, Pune {EN_DASH} 411 001 Maharashtra, India"),
    "body/p887": _ph("+91 20 6606 4494"),
    "body/p888": _p("Hitesh Ramani"),
    "body/p890": _e("hitesh.ramani@citi.com"),
    "body/p897": _a(
        "Signature Building, Bhandarkar road Shivaji Nagar, "
        f"Pune {EN_DASH} 411 004 Maharashtra, India"
    ),
    "body/p898": _ph("+91 20 2640 3100")
    + _p("Chitra Raste")
    + _u("www.eximbankindia.in/")
    + _e("pro@eximbankindia.in"),
    "body/p903": _a(f"Pune {EN_DASH} 411 001"),
    "body/p905": _ph("+91-20-26234000")
    + _p("Sharmila Joshi")
    + _u("www.indusind.com/")
    + _e("sharmila.joshi@indusind.com"),
    "body/p909": _a(f"Bund Garden Road, Pune {EN_DASH} 411 001 Maharashtra, India"),
    "body/p910": _ph("+ 91 8879770456")
    + _p("Cherag Gyara")
    + _u("www.icicibank.com")
    + _e("cherag.gyara@icicibank.com"),
    "body/p914": _a(f"Pune {EN_DASH} 411 001"),
    "body/p916": _ph("+91 20 6769 4648") + _p("Manisha Shukla") + _u("www.hdfcbank.com"),
    "body/p917": _e("manisha.shukla@hdfcbank.com"),
    "body/p921": _a(f"Pune {EN_DASH} 411 003"),
    "body/p923": _ph("+91 20 2561 8211") + _p("Tushar Wakhele") + _u("www.sbi.co.in"),
    "body/p924": _e("rm6.ifbpune@sbi.co.in"),
    "body/p929": _ph("+ 91 91586 40360"),
    "body/p930": _p("Ashish Mathew Pulloor"),
    "body/p932": _e("ashishmp@federalbank.co.in"),
    "body/p937": _ph("+91 20 7157 6403")
    + _p("Anand Soni")
    + _u("www.bajajfinance.com")
    + _e("anand.soni@bajajfinserv.in"),
    "body/p956": _a(f"Bandra Kurla Complex, Bandra (E) Mumbai {EN_DASH} 400 051, Maharashtra, India"),
}


def resolve(text: str, substring: str, occurrence: int) -> tuple[int, int]:
    """Character offsets of the nth occurrence of ``substring`` in ``text``."""
    start = -1
    for _ in range(occurrence + 1):
        start = text.find(substring, start + 1)
        if start < 0:
            raise ValueError(
                f"occurrence {occurrence} of {substring!r} not found"
            )
    return start, start + len(substring)


def build() -> list[dict]:
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    rows: list[dict] = []
    errors: list[str] = []
    unknown = set(GOLD) - {s["key"] for s in sample["segments"]}
    if unknown:
        raise SystemExit(
            "gold standard refers to segments that are not in the sample: "
            + ", ".join(sorted(unknown))
        )
    for index, segment in enumerate(sample["segments"]):
        annotations = []
        for pii_type, substring, occurrence in GOLD.get(segment["key"], []):
            try:
                start, end = resolve(segment["text"], substring, occurrence)
            except ValueError as error:
                errors.append(f"[{index}] {segment['location']}: {error}")
                continue
            annotations.append(
                {
                    "pii_type": pii_type,
                    "start": start,
                    "end": end,
                    "text": substring,
                }
            )
        annotations.sort(key=lambda a: (a["start"], a["end"]))
        # Overlapping gold spans would make the metrics ambiguous.
        for earlier, later in zip(annotations, annotations[1:]):
            if earlier["end"] > later["start"]:
                errors.append(
                    f"[{index}] {segment['location']}: overlapping gold spans "
                    f"{earlier['text']!r} and {later['text']!r}"
                )
        rows.append(
            {
                "index": index,
                "key": segment["key"],
                "stratum": segment["stratum"],
                "location": segment["location"],
                "text": segment["text"],
                "spans": annotations,
            }
        )
    if errors:
        for error in errors:
            print(f"ERROR {error}", file=sys.stderr)
        raise SystemExit(f"{len(errors)} gold-standard problem(s); nothing written")
    return rows


def main() -> int:
    rows = build()
    with OUTPUT.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    total = sum(len(row["spans"]) for row in rows)
    by_type: dict[str, int] = {}
    for row in rows:
        for span in row["spans"]:
            by_type[span["pii_type"]] = by_type.get(span["pii_type"], 0) + 1
    annotated = sum(1 for row in rows if row["spans"])
    print(f"wrote {OUTPUT}")
    print(f"  {len(rows)} segments, {annotated} with PII, {total} gold spans")
    for pii_type, count in sorted(by_type.items()):
        print(f"    {pii_type:14} {count:>4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

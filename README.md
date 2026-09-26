# PII Redaction Tool — Red Herring Prospectus

Detects personally identifiable information in a `.docx` and replaces every
mention with a **consistent fake alternative**, preserving the document's
formatting, structure and readability.

Built for the Scaler AI Labs *Environment Data* assignment against the attached
**KSH International Limited** Red Herring Prospectus (122 pages, 3,766 text
segments, 48,324 runs, 325,239 characters).

---

## Deliverables in this folder

| Path | What it is |
| --- | --- |
| `output/Red Herring Prospectus - Redacted.docx` | **the redacted document** |
| `src/piiredact/` | the redaction tool (source code) |
| `README.md` | this file — approach, trade-offs, how to run |
| `EVALUATION_REPORT.md` | evaluation method + accuracy / precision / recall |
| `evaluation_dashboard.html` | the same results as an interactive page |
| `eval/` | annotation guideline, sample, gold standard, scoring harness |
| `tests/` | 132 unit / integration tests |
| `data/` | the source documents, so the run reproduces from a clean clone |
| `output/audit_log.csv` | every replacement: location, type, detector, before → after |
| `output/mapping.csv` | the distinct real → fake table (252 entities) |
| `output/run_summary.json` | machine-readable run record |

---

## Headline result

| | Precision | Recall | F1 |
| --- | --- | --- | --- |
| **Prospectus sample** (288 hand-annotated spans) | **1.000** | **0.997** | **0.998** |
| — same, exact-boundary ("strict") scoring | 0.986 | 0.983 | 0.984 |
| **Synthetic corpus** (SSN / card / DOB / IP) | **1.000** | **1.000** | **1.000** |

Character-level accuracy on the prospectus sample: **0.9983**
(8,711 of 59,399 characters are PII; 22 false-positive and 77 false-negative
characters).

On the full document: **681 mentions of 332 distinct entities replaced**, and an
independent re-scan of the written file finds **0** of the 252 original values
still present.

Full method, per-type tables, error analysis and ablations: `EVALUATION_REPORT.md`.

---

## Approach

A hybrid of **regex + document structure + lexicons + spaCy NER**, arranged as a
two-pass pipeline. No single technique was enough, and the measurements that led
to that conclusion are in the report; the short version:

* **Regex alone** over-fires badly here. A prospectus is dense with 8–16 digit
  runs that are *not* phone numbers or card numbers — DINs, CINs, SEBI
  registration numbers, PIN codes, share counts, rupee amounts.
* **spaCy NER alone** under-performs in both directions. On this document
  `en_core_web_md` returns 60 distinct `PERSON` strings, of which roughly a third
  are address fragments (`Village Birdewadi`, `Bandra Kurla Complex`) or document
  jargon (`S. No`, `Cap Price`, `Fiscals`) — while missing most of the directors,
  because they appear in table cells rather than in sentences. Its `ORG` output is
  worse: the top entries are `Offer`, `this Red Herring Prospectus` and
  `Fiscals 2025`.
* **The document's own layout is the strongest signal available** and costs
  nothing. A cell under a column headed `DIN` *is* a Director Identification
  Number; a cell under `Address` *is* a postal address. Carrying the table header
  down to the detectors is the single biggest reason the structured tables redact
  cleanly.

### The pipeline

```
read .docx ──► NER pre-pass ──► PASS 1 detect ──► PASS 2 sweep ──► resolve
   (run-level                   (precision-first)  (recall)      (disjoint plan)
    offset map)                                                        │
                                                                       ▼
   write runs in place ◄── surrogates ◄──────────────────────── replacement plan
   (formatting intact)      (consistent fakes)
```

**1. Read.** The body's XML children are walked in document order so paragraphs,
tables and nested tables stay in reading order. Each segment carries a
**run-level offset map** — which slice of the segment's text each `w:r` element
owns — because that is what allows a redaction to be written back without
destroying character formatting. Table cells additionally carry their column
header and row label.

**2. Pass 1 — precision-first detection.** Twelve detectors, one module per PII
type, each emitting only what it is confident about:

| Detector | Method |
| --- | --- |
| `email.regex` | regex; also derives person-name hints from `first.last@` local parts |
| `url.regex` | regex + a public-domain allowlist |
| `phone.pattern` | three narrow sources (label-anchored, country-code, trunk-prefix) + `phonenumbers` plausibility check |
| `ssn.pattern` | SSA allocation rules (area ≠ 000/666/9xx, group ≠ 00, serial ≠ 0000) |
| `credit_card.luhn` | Luhn check digit + issuer-prefix match + a round-number guard |
| `ip.pattern` | `ipaddress` validation + version/clause-number vetoes |
| `national_id.rules` | DIN, CIN, PAN, Aadhaar (Verhoeff checksum), GSTIN, SEBI/ICAI registrations, peer-review, passport, DL, voter ID |
| `dob.contextual` | dates only where a birth cue ties them to a person |
| `address.anchored` | anchor on the postal code, expand left to a structural boundary |
| `organization.suffix` | anchor on the legal suffix (`Private Limited`, `LLP`, `Family Trust`…), expand left; plus defined-alias and brand-acronym derivation |
| `person.hybrid` | four strategies: table column header → cue labels → honorifics → filtered NER |
| `gazetteer.sweep` | *(pass 2, below)* |

**3. Pass 2 — the gazetteer sweep.** Pass 1's precision comes from only firing
where the document makes an entity structurally obvious, which leaves most
*mentions* untouched: `Kushal Subbayya Hegde` is caught in the board table, but the
same person is also `Mr. Hegde` in the risk factors and `Kushal Hegde` in the
capital structure. So pass 1 registers every entity it finds in a shared
gazetteer; pass 2 expands that list with derived surface forms (surnames,
first-plus-last contractions, brand acronyms) and re-scans the whole document for
them. Because it only looks for entities already *proved* present, it raises
recall a long way at no precision cost. **Ablation: removing it costs 2.1 points
of recall on the sample and 2.6 of strict F1.**

Three details matter in practice: whitespace-flexible matching (Word stores
`Chairman\tand\tExecutive Director`, and names break across tabs and line breaks
too); longest-first alternation, so `Kushal Subbayya Hegde` beats `Hegde`; and
**case-sensitive matching for short initialisms** — matching a 4-letter acronym
case-insensitively once redacted 43 occurrences of the ordinary English word
"care" because `CARE` is a real short form of CARE Ratings.

**4. Resolve.** Twelve detectors produce 1,344 overlapping claims over the
document; four documented rules reduce them to 681 disjoint spans — confidence
floor, duplicate merge (536 merges, which is how the audit log can show that two
independent strategies agreed), containment, and partial-overlap truncation. The
replacement stage assumes its input is disjoint and sorted, and the resolver
asserts that.

**5. Surrogates.** See below.

**6. Write.** Replacements are written *into the original runs*: the surrogate
goes into the first run the span touches, and the span's characters are removed
from the rest. `paragraph.text = ...` would have been one line and would have
destroyed every bold, font, size and language mark in the paragraph. 2,478 runs
were modified across 371 segments; paragraph, table and section counts are
identical before and after, and all 8 embedded images survive.

**7. Verify.** Every rewritten segment is re-read from the live runs and checked
for surviving original values. The run reports `residual_original_values: 0`.

### Surrogate generation

The assignment asks for *fake alternatives*, which is a stronger requirement than
it looks. Four properties, all implemented and all visible in `mapping.csv`:

1. **Consistency** — every mention of one entity maps to the same surrogate,
   keyed on the gazetteer's canonical form. Without this the document becomes
   unreadable *and* leaks structure (a reader could count distinct names).
2. **Part-wise consistency** — the gazetteer knows `Hegde` is the surname of
   `Kushal Subbayya Hegde`, so the bare surname becomes the *surname of that
   person's surrogate*:
   `Kushal Subbayya Hegde → Samar Sami Barad`, `Kushal Hegde → Samar Barad`.
   Same for companies: `KSH International Limited → Kailash Cables Limited`,
   `KSH International → Kailash Cables`, `KSH → KAILASH`.
3. **Cross-type coherence** — `cherag.gyara@icicibank.com` becomes
   `niharika.boase@example.com`, matching `Cherag Gyara → Niharika Boase`; a
   company's website agrees with its new name.
4. **Format preservation** — `+91 22 6807 7100 → +91 22 2219 9050` (same country
   code, grouping and length); `KSH INTERNATIONAL LIMITED` keeps its upper case;
   a `Private Limited` stays a `Private Limited`; an address keeps its element
   count, its PIN-code shape and its `, India` tail.

Safety: generation is deterministic given `--seed`, so a run is reproducible and
reviewable; a surrogate is never equal to the value it replaces; emails and URLs
use the RFC 2606 reserved domains; IP addresses use the RFC 5737 / RFC 3849
documentation ranges; generated Aadhaar numbers carry a valid Verhoeff check digit
and generated card numbers are Luhn-valid, so downstream validators still accept
the redacted file. Document metadata (author, company, last-modified-by) is
blanked as part of the run — redacting the body and shipping the drafting lawyer's
name in `docProps/core.xml` would defeat the exercise.

---

## Precision policy — the explicit choices

The assignment asks us to be explicit about what we treat as PII. Every choice
below is enforced by a lexicon in `src/piiredact/lexicons.py`, not by scattered
code, so a reviewer can audit the policy in one file.

**Redacted.** Names of natural persons (directors, promoters, KMPs, contact
persons at intermediaries, individual shareholders, the chartered engineer);
emails; phones; postal addresses; **private** legal entities that are parties to
the transaction (the issuer, its subsidiaries and group entities, the promoter
trusts, the BRLMs, the registrar, the bankers, the auditors, legal counsel, the
rating agency, named customers/suppliers/competitors) and their websites.

**Also redacted, beyond the assignment's minimum list — and here is why.**
Government and regulatory identifiers: DIN, CIN, PAN, Aadhaar, GSTIN, SEBI and
ICAI registration numbers, peer-review and membership numbers.
*A redaction that removes a name and leaves behind a unique key pointing at that
name is not a redaction.* In this document every director's name sits in a table
cell next to their DIN, and every intermediary's name sits next to its SEBI
registration number; either re-identifies the party in one lookup against a
public register.

**Not redacted.** Regulators, ministries, courts and tribunals (SEBI, RBI, RoC,
MCA, NCLT, Government of India, the SEC); stock exchanges and depositories (BSE,
NSE, NSDL, CDSL, NPCI); standard-setters and codified frameworks (ICAI, Companies
Act, Ind AS, IFRS); **roles** defined by the offer rather than the entities
filling them (`Escrow Collection Bank`, `Book Running Lead Managers`,
`Statutory Auditors`, `Promoter Group`); newspaper titles named as the statutory
advertising medium; job titles; share counts, amounts and percentages;
incorporation, resolution, fiscal and bid dates.

These are matters of public record and redacting them would destroy the
document's legal meaning without protecting anyone. The concrete consequence is
that `BSE Limited` survives while `HDFC Bank Limited` does not, and that is
deliberate: the first is market infrastructure, the second is a commercial
counterparty.

**Order and ticket numbers** — the assignment's example of something *not* to
redact. There are none in this corpus, and nothing here treats a bare reference
number as PII. The `national_id` detector fires only on checksum-validated or
label-anchored formats, which is what keeps `Peer review number: 014680` in and
`Order number 4111 1111 1111 1112` out.

---

## Trade-offs, false positives and false negatives

Measured, not guessed. Every item below is reproducible from
`eval/results/metrics.json`.

### What the tool gets wrong

**1. Address boundaries (the main residual error).** 3 of 47 gold addresses are
matched with the wrong extent — relaxed recall 1.000, strict recall 0.936. All
three are the same conflict: the address detector treats a legal-entity suffix as
its left boundary, because in every "who to contact" block the company name sits
directly above the address (`ICICI Securities Limited` / `ICICI Venture House,
Appasaheb Marathe Marg…`). That rule is right for the common case and wrong for
a company name used as a *landmark* (`above HDFC Limited Karve Road`). A
preposition guard handles the landmark case, but where the two rules still collide
the address is clipped. **Privacy impact: none** — both values are redacted
either way; the cost is that the surrogate reads slightly less naturally.

**2. `Waterloo Motors` missed in one cell** — the single recall miss. This is an
artifact of *how the sample is scored*, not of the tool: pass 2 is document-wide,
but the sample-based evaluation gives the gazetteer only the 301 sampled segments,
so the alias `Waterloo Motors` is never derived from
`Waterloo Motors Private Limited`. On the full document it **is** redacted (it
appears in `mapping.csv`). The evaluation therefore *understates* real recall
slightly, which is the right direction for an error to run.

**3. Type confusion on trading names.** `Kushal Electricals` is a partnership firm
named after a person; the tool labels it `PERSON`, the guideline says
`ORGANIZATION`. It is redacted either way and gets a consistent surrogate, so the
only cost is a wrong label in the audit log. Likewise `Karunakar Hegde HUF` is
labelled `PERSON` including the `HUF` entity suffix.

**4. Known, accepted false-negative classes.**

* **Dates of birth in free prose with no birth cue.** `dob.contextual` requires a
  `Date of Birth` / `DOB` / `born on` cue or a `(b. 1953)` form. There are 147
  distinct dates in this document and not one is a DOB; treating "any date" as a
  DOB would have destroyed the document for zero gain. `--dob-mode=contextual`
  widens this for callers who prefer over-redaction.
* **Ages** (`aged 72 years`) are quasi-identifiers but sit outside the
  assignment's type list, so they are deliberately out of scope.
* **Government schemes named after people.** `Deen Dayal Upadhyaya Gram Jyoti
  Yojana` looks exactly like an Indian personal name. A scheme-word lookahead
  suppresses it — correctly, but a scheme named after a person in a way the word
  list does not cover would be redacted.

### False positives that were found and fixed

These are worth listing because they are the ones a regex-only or NER-only
solution ships with. Each was found by reading the audit log and each is now
prevented by a named rule:

| Symptom | Cause | Fix |
| --- | --- | --- |
| 261 occurrences of "equity" redacted | a heading `EQUITY SHARES … OF KSH INTERNATIONAL LIMITED` was parsed as a company, yielding the alias `EQUITY` | initialisms capped at 5 characters and screened against the generic-word lexicon |
| 43 occurrences of the word "care" redacted | `CARE` is a real short form of CARE Ratings, matched case-insensitively | short all-caps initialisms are swept **case-sensitively** |
| `TOTAL OFFER SIZE` flagged as a person | a cover-page layout table has a `CONTACT PERSON` column | a phrase built only of generic commercial vocabulary is never a name |
| `PAT CAGR`, `Depositories Act`, `SECTION III` flagged as people | NER plus a permissive name shape | financial-acronym, legal-instrument and structural-word vetoes |
| `HEGDE, RAKHI GIRIJA SHETTY, DHAULAGIRI FAMILY TRUST` as one organisation | left expansion crossed commas | a comma or closing quote ends a company name |
| `Escrow Collection Bank HDFC Bank` as one organisation, starving the real `HDFC Bank Limited` | expansion walked through a contractual role phrase | leading role-phrase and generic-word trims, applied until stable |
| `Chitra Raste Website` as a person | `Website:` follows the name with no punctuation | trailing field-label words are stripped |
| `bank holidays`, `Liabilities of the Company` as organisations | weak suffixes (`Bank`, `Company`) anchoring on their own | a weak suffix must be capitalised and have a distinctive capitalised word to its left |
| `National Securities` / `Depository Limited` fragments | a public-body name split by the terminator rule | candidates overlapping a public-body match are vetoed |

### Two bugs worth naming, because they are the ones that actually leak

Both were caught by the independent post-write re-scan, not by the unit tests —
which is why that re-scan exists.

* **`Paragraph.runs` is not exhaustive.** It returns only *direct* `w:r` children,
  silently skipping runs nested in `w:hyperlink`, `w:sdt` (content controls) and
  `w:ins` (tracked insertions). Those are displayed text, so the detectors never
  saw the characters and the writer never rewrote them. The reader now walks all
  `w:r` descendants and skips only `w:del` / `w:moveFrom`, which hold
  *undisplayed* text.
* **`id(cell._tc)` is not a stable identity.** lxml hands out *transient* element
  proxies, so a freshly collected proxy can reuse an earlier object's `id`. The
  merged-cell de-duplication keyed on it and therefore dropped one shareholder
  name cell from the entire run. Keying on the element's XML path fixed it and
  recovered 56 previously-invisible table cells.

### Performance

~71 seconds end to end for the 122-page document on a laptop CPU, of which 44 s
is loading and running spaCy. Detection itself is 12.6 s, resolution 0.03 s,
writing 1.7 s. `--no-ner` runs in ~25 s and costs 0.4 points of recall on the
sample.

---

## Extending to a new PII type

Four steps, no changes anywhere else — detection, resolution, replacement, the
audit log and the evaluation harness all read the type off the span:

1. add the member to `PIIType` and give it a priority in `TYPE_PRIORITY`
   (`src/piiredact/types.py`);
2. write `src/piiredact/detectors/<name>.py` with a `@register`-decorated class
   (subclass `BaseDetector`, implement `detect(segment, ctx)`);
3. import it in `src/piiredact/detectors/__init__.py`;
4. add a generator to `SurrogateEngine._generators`
   (`src/piiredact/surrogates.py`).

`python -m piiredact detectors` confirms it is wired up. To make it exhaustive
across the document rather than local, add it to `SWEPT_TYPES` in
`detectors/gazetteer.py`.

---

## Running it

```bash
pip install -r requirements.txt
python -m spacy download en_core_web_md      # or the wheel URL in requirements.txt

# reproduce the deliverable exactly
set PYTHONPATH=src                            # Windows;  export on POSIX
python -m piiredact redact "data/Red Herring Prospectus.docx" \
       "output/Red Herring Prospectus - Redacted.docx" --report-dir output

# detect without writing
python -m piiredact scan "data/Red Herring Prospectus.docx" --show 20

# list the detector registry
python -m piiredact detectors
```

Useful flags: `--types person,email,phone` to restrict scope, `--seed X` to
re-roll the fake values, `--min-confidence`, `--no-ner` / `--no-sweep` for
ablations, `--dob-mode contextual`, `--disable <detector>`, `--keep-metadata`.

Reproducing the evaluation:

```bash
python eval/sample.py                     # rebuild the stratified sample
python eval/gold_standard.py              # resolve annotations -> gold_standard.jsonl
python eval/evaluate.py                   # metrics + ablations -> eval/results/
python -m pytest tests -q                 # 132 tests
```

### Dependencies

`python-docx` (read/write), `spaCy` + `en_core_web_md` (NER), `Faker` (surrogate
names and places), `phonenumbers` (phone plausibility). Python 3.10+.
Both `phonenumbers` and spaCy are optional at import time — the tool degrades to
regex-plus-structure without them rather than failing.

**Not used: Presidio.** It would have supplied the regex detectors and a spaCy
wrapper, which is the part of this problem that was already easy. What this corpus
actually needed was table-header awareness, an Indian-address anchor-and-expand
strategy, the legal-suffix organisation strategy, a public-body allowlist, and
entity-consistent surrogates — none of which comes out of the box, and all of
which would have had to be written as custom recognizers and operators anyway. A
direct implementation is also far easier to explain, which the assignment asks for.

---

## Repository layout

```
src/piiredact/
  types.py          Span, Segment, PIIType, type priorities
  config.py         one frozen dataclass describing a run
  lexicons.py       the precision policy: allowlists, suffixes, veto vocabularies
  resolve.py        overlap resolution -> a disjoint, sorted plan
  surrogates.py     consistent, format-preserving fake-value generation
  pipeline.py       stage functions + the end-to-end run
  cli.py            redact / scan / detectors
  detectors/        one module per PII type, plus base.py (protocol, registry,
                    gazetteer) and gazetteer.py (the pass-2 sweep)
  docio/            docx_reader.py (run-level offset maps), docx_writer.py
eval/               annotation_guideline.md, sample.py, gold_standard.py,
                    evaluate.py, results/
tests/              detectors, surrogates, resolver, docx round-trip, pipeline
```

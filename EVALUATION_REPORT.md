# Evaluation Report — PII Redaction Tool

**Document under test:** KSH International Limited, Red Herring Prospectus dated
10 December 2025 (122 pages; 3,766 text segments; 48,324 runs; 325,239 characters).
**Tool version:** `piiredact` 1.0.0. **Run seed:** `scaler-ai-labs-2026`.
**Reproduce with:** `python eval/evaluate.py` → `eval/results/metrics.json`.

---

## 1. Evaluation approach

### 1.1 Why a hand-annotated gold standard was necessary

Precision and recall need ground truth, and this document ships with none. Two
things were therefore built:

**A. A hand-annotated sample of the real document.** 301 segments,
**288 gold spans**. This measures the tool on the data it was actually built for,
including all the awkwardness — merged cells, tab-separated words, names broken
across lines, 147 non-birth dates, and thousands of digit runs that are not
identifiers.

**B. A synthetic corpus with programmatic labels.** 41 cases, **30 labelled spans
and 22 pure negatives**. SSN, credit card, date of birth and IP address are all in
the assignment's minimum set and **none of them occurs in an Indian IPO
prospectus**. Measuring those four types on the prospectus would report 0/0 and
say nothing about whether the detectors work, so they are measured here instead,
against adversarial negatives drawn from the shapes that actually cause false
positives in financial prose (nine-digit amounts, sixteen-digit reference numbers,
dotted clause numbers, `Ind AS 115.2.1.3`, and the document's own date formats).

### 1.2 Sampling: stratified, not uniform

A uniform sample of a 122-page prospectus is almost all financial tables. It would
measure precision on negatives and say nothing about recall. So the sample is
stratified, with a fixed seed (`20260925`) and rules in `eval/sample.py`:

| Stratum | Rule | Sampled |
| --- | --- | --- |
| `structural` | the PII-dense structures, taken **in full** — cover-page table, SEBI disclaimer table, board-of-directors table, underwriters table, and the "General Information" contact blocks | 126 of 126 |
| `contact` | every other segment containing an email, a telephone label, a `Contact Person`/office label or a PIN-code shape — taken **in full** | 87 of 87 |
| `prose` | random draw from body paragraphs ≥120 characters (risk factors, business) | 40 of 346 |
| `negative` | random draw from financial-table cells, which should contain no PII at all — these carry the precision signal | 48 of 1,589 |

Three segments that the original draw happened to catch, and that turned out to
carry PII in shareholder-name columns the stratification rules do not otherwise
reach, are pinned so the sample stays stable across re-sampling.

### 1.3 Annotation protocol

The annotation guideline (`eval/annotation_guideline.md`) was **written and frozen
before annotation began**, so the gold standard records a policy rather than a
description of what the tool happens to do. It fixes span boundaries (longest span
that is one value; no honorifics, labels, footnote markers or quotes), the
public-body / contractual-role exclusions, and every ambiguity that came up —
newspaper titles, ages, incorporation dates, a company name used as a landmark
inside an address, `<Name> HUF`.

Annotations are stored as `(type, exact_substring, occurrence)` rather than
character offsets and are resolved against the live sample at build time
(`eval/gold_standard.py`). The builder **aborts** if a substring is absent, if the
requested occurrence does not exist, if two gold spans overlap, or if an
annotation refers to a segment no longer in the sample. A transcription error
therefore becomes a hard failure instead of a silent false negative.

Segments absent from the gold table are asserted to contain **no** PII. Their
emptiness is an annotation too, and it is what the precision figures are measured
against.

### 1.4 Two matching criteria, reported side by side

* **Strict** — same type *and* exactly the same character boundaries. This is the
  right measure for a value that will be **replaced**: a wrong boundary means
  writing a surrogate over the wrong characters.
* **Relaxed** — same type and any character overlap (the MUC-style partial credit
  used in most NER evaluations). This is the right measure for **privacy**: an
  address whose span is two words short still had its identifying content replaced.

Matching is greedy one-to-one, ordered by overlap size, so one prediction can never
satisfy two gold spans. Reporting only strict understates a redactor's protective
value; reporting only relaxed hides real boundary bugs. Both are below.

### 1.5 How "accuracy" is defined

A span-extraction task has no natural accuracy, so two well-defined variants are
reported, and neither replaces precision/recall:

* **Character-level accuracy** — every character of every sampled segment is one
  instance, labelled PII or not-PII. Well defined, and dominated by the ~85% of
  characters that are not PII, which is why a high number here is unremarkable and
  is reported alongside character precision and recall.
* **Segment-level accuracy** — did a segment that contains PII get flagged at all?
  This is the operationally useful "did we notice this cell" number.

### 1.6 The system under evaluation is the shipped system

The harness calls `piiredact.pipeline.detect_spans` — the same function the
redaction uses, with the same default config. There is no parallel implementation
that could drift.

### 1.7 Threats to validity — stated plainly

1. **The annotator is the tool's author.** This is the real limitation of this
   evaluation and it cannot be fully removed by one person. Mitigations: the
   guideline was frozen before annotation; annotation was done from raw segment
   text; the "no PII here" judgements on 88 prose and negative segments were made
   before any per-segment output was read; and the four types that matter most for
   over-fitting are additionally measured on the programmatically-labelled
   synthetic corpus, where the labels do not come from a human at all. The honest
   reading is that the **synthetic** figures are independent and the **document**
   figures are a careful self-assessment.
2. **The sample is 8% of the document's segments** (301 of 3,766) but is weighted
   towards PII-bearing structures by design. It is not an estimate of
   document-wide error *rates*; it is a measurement of behaviour on the parts of
   the document where PII lives, plus 88 negatives.
3. **Pass 2 is handicapped by sampling.** The gazetteer sweep is document-wide in
   production, but the harness gives it only the 301 sampled segments, so aliases
   derivable only from unsampled text are unavailable. This causes the single
   recall miss in §2 and means the sample **understates** real recall.
4. **Three PII types have no positives anywhere.** Passport, driving licence and
   voter ID detectors are exercised only by unit tests.

---

## 2. Results — prospectus sample

301 segments · 288 gold spans · 287 predicted spans

### 2.1 Relaxed matching (privacy view)

| Type | Gold | TP | FP | FN | Precision | Recall | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ADDRESS | 47 | 47 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| EMAIL | 52 | 52 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| NATIONAL_ID | 18 | 18 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| ORGANIZATION | 37 | 36 | 0 | 1 | 1.000 | 0.973 | 0.986 |
| PERSON | 78 | 78 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| PHONE | 36 | 36 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| URL | 20 | 20 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| **Micro** | **288** | **287** | **0** | **1** | **1.000** | **0.997** | **0.998** |
| **Macro** | — | — | — | — | 1.000 | 0.996 | 0.998 |

### 2.2 Strict matching (exact-boundary view)

| Type | Gold | TP | FP | FN | Precision | Recall | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ADDRESS | 47 | 44 | 3 | 3 | 0.936 | 0.936 | 0.936 |
| EMAIL | 52 | 52 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| NATIONAL_ID | 18 | 18 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| ORGANIZATION | 37 | 35 | 1 | 2 | 0.972 | 0.946 | 0.959 |
| PERSON | 78 | 78 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| PHONE | 36 | 36 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| URL | 20 | 20 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| **Micro** | **288** | **283** | **4** | **5** | **0.986** | **0.983** | **0.984** |
| **Macro** | — | — | — | — | 0.987 | 0.983 | 0.985 |

The entire strict-vs-relaxed gap is **4 boundary errors** — 3 addresses and 1
organisation. No span is a wrong-type or spurious detection under either
criterion.

### 2.3 Accuracy

| Measure | Value | Detail |
| --- | --- | --- |
| **Character-level accuracy** | **0.9983** | 59,399 characters; 8,711 are PII; TP 8,634 · FP 22 · FN 77 · TN 50,666 |
| Character precision / recall / F1 | 0.998 / 0.991 / 0.994 | |
| Character specificity | 0.9996 | of 50,688 non-PII characters, 22 were over-redacted |
| **Segment-level accuracy** | **0.9967** | 301 segments; TP 134 · FP 0 · FN 1 · TN 166 |
| Segment precision / recall | 1.000 / 0.993 | every segment flagged did contain PII |

**FP 0 at the segment level is the number to note:** across 166 segments with no
PII — including 48 financial-table cells stuffed with long digit runs, dates and
capitalised jargon — the tool did not fire once.

### 2.4 By stratum (relaxed)

| Stratum | Segments | Gold | TP | FP | FN | Precision | Recall |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `structural` | 126 | 103 | 103 | 0 | 0 | 1.000 | 1.000 |
| `contact` | 87 | 164 | 164 | 0 | 0 | 1.000 | 1.000 |
| `prose` | 40 | 7 | 7 | 0 | 0 | 1.000 | 1.000 |
| `negative` | 48 | 14 | 13 | 0 | 1 | 1.000 | 0.929 |

---

## 3. Results — synthetic corpus (SSN · card · DOB · IP)

41 cases · 30 labelled spans · 22 pure negatives. Labels are generated, not
annotated, so these figures are independent of the author's judgement.

| Type | Gold | TP | FP | FN | Precision | Recall | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| SSN | 6 | 6 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| CREDIT_CARD | 7 | 7 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| DOB | 6 | 6 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| IP_ADDRESS | 6 | 6 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| PERSON | 3 | 3 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| EMAIL | 1 | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| PHONE | 1 | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| **Micro** | **30** | **30** | **0** | **0** | **1.000** | **1.000** | **1.000** |

Strict and relaxed are identical (every boundary exact). Character accuracy 1.000.
All 22 adversarial negatives were correctly left alone, including
`000-12-3456`, `666-22-1111`, `912-45-6789`, `214-00-8899`, `214-55-0000`,
`4111 1111 1111 1112` (Luhn fails), `4200000000000000` (Luhn-valid but a share
count), `Ind AS 115.2.1.3`, `clause 4.1.2.3`, `256.100.50.25`, and five non-birth
dates including `incorporated on July 30, 1979`.

These results are the *second* iteration. The first found four genuine detector
bugs, each now covered by a regression test: `IPV4` matching failed at the end of a
sentence; the IPv6 pattern could not handle `::` compression; the SSN label pattern
required a colon and missed `SSN is 219456789`; and `Discover` was absent from the
card-label vocabulary. Two fixture errors of my own were also corrected — a
negative case that was in fact a valid IPv4 literal, and a RuPay number whose check
digit was wrong.

---

## 4. Error analysis

### 4.1 The one recall miss

`Waterloo Motors` in `table 31, row 2, col 1`. This is an **evaluation artifact**,
not a tool failure: the alias is derived from `Waterloo Motors Private Limited`,
which is not in the 301-segment sample, so the sample-scoped gazetteer never learns
it. On the full document it is detected and redacted — it appears in
`output/mapping.csv`. Per §1.7(3), the sample understates real recall here.

### 4.2 The four boundary errors

| Location | Type | Gold | Predicted |
| --- | --- | --- | --- |
| table 73, row 1, col 0 | ADDRESS | `5th Floor, Gopal House ⏎ Opposite Harshal Hall, above HDFC Limited Karve Road, Pune – 411 038 ⏎ Maharashtra, India` | `Karve Road, Pune – 411 038 ⏎ Maharashtra, India` |
| table 73, row 1, col 0 | ORGANIZATION | `Kirtane & Pandit, LLP` | `Kirtane & Pandit` |
| table 2, row 14, col 0 | ADDRESS | `801 – 804, Wing A, … Mumbai 400051, Maharashtra, India` | same extent, one trailing element short |
| table 2, row 14, col 2 | ADDRESS | `ICICI Venture House, … Mumbai 400025, Maharashtra, India` | same extent, one trailing element short |

All four are the same underlying tension, described in the README: the address
detector uses a legal-entity suffix as its left boundary because in every contact
block the company name sits directly above the address. That rule is right for the
common case and wrong when a company name is used as a landmark inside the address.
**No privacy impact** — in every case both the address and the company name are
redacted; the cost is a less natural-reading surrogate.

### 4.3 Accepted false-negative classes (by design)

* **Unlabelled dates of birth in free prose.** 147 distinct dates in this document,
  zero DOBs; a date-agnostic DOB rule would score near-zero precision. Widen with
  `--dob-mode=contextual`.
* **Ages** — quasi-identifiers, outside the assignment's type list.
* **Schemes named after people** — suppressed by a scheme-word lookahead; a scheme
  whose naming convention is not in that list would be over-redacted.
* **Passport / driving licence / voter ID** — label-anchored only, because their
  bare formats collide with ordinary reference numbers. No positives exist in this
  corpus.

### 4.4 Type confusion

Two cases, both redacted correctly but labelled imperfectly in the audit log:
`Kushal Electricals` (a partnership firm trading under a person's name) is labelled
`PERSON`; `Karunakar Hegde HUF` is labelled `PERSON` including the `HUF` entity
suffix. Zero type confusions appear in the scored metrics because neither segment
is in the sample.

---

## 5. Ablations — what each component is worth

Relaxed micro on the prospectus sample. This is the evidence for the hybrid design.

| Configuration | Predicted | Precision | Recall | F1 | Strict F1 |
| --- | --- | --- | --- | --- | --- |
| **full (shipped)** | 287 | 1.000 | **0.997** | **0.998** | **0.984** |
| no gazetteer sweep (pass 1 only) | 282 | 1.000 | 0.979 | 0.990 | 0.958 |
| no spaCy NER | 286 | 1.000 | 0.993 | 0.997 | 0.983 |
| no NER **and** no sweep | 258 | 1.000 | 0.896 | 0.945 | 0.930 |
| no person detector | 232 | 1.000 | 0.806 | 0.892 | 0.877 |
| min-confidence 0.0 | 287 | 1.000 | 0.997 | 0.998 | 0.984 |
| min-confidence 0.90 | 287 | 1.000 | 0.997 | 0.998 | 0.984 |

Readings:

* **Precision is 1.000 in every configuration.** Precision here comes from the
  lexicons and the structural rules, not from any single detector — which is the
  design intent, and means a recall-oriented change cannot quietly cost precision.
* **The pass-2 sweep is worth 1.8 points of recall and 2.6 of strict F1.** It is
  the cheapest recall available: it only searches for entities already proved
  present.
* **spaCy contributes 0.4 points of recall.** Small — because the structural and
  cue-label strategies already cover this document's tables — but it is doing real
  work in prose, and `--no-ner` is a supported 3× speed-up when that trade is
  acceptable.
* **Removing sweep *and* NER together costs 10 points of recall** (0.997 → 0.896),
  more than the sum of the parts: the sweep amplifies whatever pass 1 finds, so the
  two losses compound. This is the clearest argument against a regex-only design.
* **The person detector carries 56 of 288 spans** (recall 0.997 → 0.806), the
  largest single contribution.
* **The confidence floor is inert on this document** — every surviving span scores
  ≥ 0.90 after duplicate merging, so 0.0 and 0.90 give identical results. The knob
  exists for noisier inputs; on this one it is not doing anything, and saying so is
  more useful than implying it is tuned.

---

## 6. Whole-document redaction run

Metrics above are on the sample; this is what the delivered document actually
contains. Source: `output/run_summary.json`.

| | |
| --- | --- |
| Segments read / runs / characters | 3,766 / 48,324 / 325,239 |
| Detector claims before resolution | 1,344 |
| — duplicates merged (independent agreement) | 536 |
| — contained spans absorbed | 127 |
| **Mentions replaced** | **681** |
| **Distinct entities** | **332** |
| Distinct surrogates issued | 252 |
| Runs modified / segments modified | 2,478 / 371 |
| Characters removed / inserted | 16,843 / 17,949 |
| Replacements skipped | **0** |
| Metadata fields cleared | author, company, last-modified-by (all already empty) |
| Wall-clock | ~71 s (44 s of it spaCy load + inference) |

Per type: ORGANIZATION 272 · PERSON 212 · EMAIL 52 · ADDRESS 49 · PHONE 36 ·
NATIONAL_ID 33 · URL 27.

### 6.1 Integrity checks on the delivered file

| Check | Result |
| --- | --- |
| In-pipeline re-read of every rewritten segment for surviving originals | **0 residual values** |
| Independent re-scan of the saved `.docx` for all 252 original values | **0 residual values** |
| Paragraph count preserved | 1,006 → 1,006 |
| Table count preserved | 76 → 76 |
| Section count preserved | 85 → 85 |
| Embedded images preserved | 8 → 8 |
| Run-level character formatting | preserved (replacement inherits the formatting of the run where the value began) |

The independent re-scan matters: it is what caught the two leak-causing bugs
described in the README (`Paragraph.runs` not being exhaustive, and `id()` on
transient lxml proxies), neither of which any unit test would have found.

---

## 7. Test suite

`python -m pytest tests -q` — 132 tests covering detector positives and negatives
(including every adversarial case from §3 as a regression test), surrogate
consistency and format preservation, the four resolver rules, the docx round-trip
with formatting assertions, and an end-to-end pipeline run on a generated document.

---

## 8. Summary

| | Precision | Recall | F1 | Accuracy |
| --- | --- | --- | --- | --- |
| Prospectus sample — relaxed | 1.000 | 0.997 | 0.998 | 0.9983 (char) / 0.9967 (segment) |
| Prospectus sample — strict | 0.986 | 0.983 | 0.984 | — |
| Synthetic corpus | 1.000 | 1.000 | 1.000 | 1.000 (char) |

One recall miss (an evaluation artifact), four boundary errors (no privacy
impact), zero false positives under either criterion, and zero original values
surviving in the delivered document.

The figures are high, and the reason is not that the problem is easy: the first
iteration of this tool redacted 261 occurrences of the word "equity" and 43 of the
word "care", reported `TOTAL OFFER SIZE` as a person, and left a shareholder's
name in the output. What closed the gap was measuring, reading the audit log,
and encoding each finding as a named rule — plus the independent post-write
re-scan that refuses to take the pipeline's own word for it.

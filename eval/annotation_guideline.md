# Annotation guideline (v1, fixed before annotation began)

This document defines what counts as PII in the evaluation sample. It was written
and frozen **before** the gold standard was annotated, so that the gold standard
records a policy rather than a description of what the tool happens to do. Where
the tool and this guideline disagree, the guideline is right and the disagreement
is a scored error.

Annotations are written as `(pii_type, exact_substring, occurrence)` rather than
character offsets: `occurrence` is 0 for the first appearance of that substring in
the segment, 1 for the second, and so on. The evaluator resolves these to offsets
and aborts if a substring cannot be found, which makes a typo in the gold standard
a hard failure rather than a silent false negative.

## Span boundaries

* Annotate the **longest** span that is one value. `Kushal Subbayya Hegde` is one
  PERSON, not three.
* Do **not** include titles, honorifics or designations in a PERSON span.
  In `Mr. Rajesh Kushal Hegde, Managing Director` the span is
  `Rajesh Kushal Hegde`.
* Do **not** include a label in the value. In `Telephone: +91 22 6807 7100` the
  span is `+91 22 6807 7100`.
* Do **not** include trailing footnote markers (`*`, `#`, `^`) or surrounding
  quotes and brackets.
* Where a value is split by a tab or a line break inside the segment, annotate it
  including the whitespace exactly as it appears.

## PERSON

Annotate any name of a natural person: directors, promoters, key managerial
personnel, contact persons at intermediaries, signatories, individual
shareholders, the chartered engineer.

* Bare surnames and first-plus-last contractions count when they refer to a
  specific individual (`Hegde` in `Mr. Hegde`, `Kushal Hegde`).
* `<Name> HUF` — annotate the personal-name part only; `HUF` is an entity suffix.
* A government scheme named after a person (`Deen Dayal Upadhyaya Gram Jyoti
  Yojana`) is **not** a PERSON.
* A partnership firm trading under a person's name (`Kushal Electricals`) is an
  ORGANIZATION, not a PERSON.

## ORGANIZATION

Annotate the name of any **private** legal entity that is a party to, or named
in, the transaction: the issuer, its subsidiaries and group entities, the
promoter trusts, the book-running lead managers, the registrar, the bankers, the
auditors, the legal counsel, the credit-rating agency, named customers,
suppliers and competitors.

Include the legal suffix (`Private Limited`, `LLP`, `Family Trust`).

Do **not** annotate:

* regulators, ministries, courts, tribunals and units of government (SEBI, RBI,
  RoC, MCA, NCLT, Government of India, the SEC);
* stock exchanges and depositories (BSE Limited, National Stock Exchange of India
  Limited, NSDL, CDSL, NPCI);
* standard-setters and codified frameworks (ICAI, Companies Act, Ind AS, IFRS);
* **roles** defined by the offer rather than entities filling them
  (`Escrow Collection Bank`, `Book Running Lead Managers`, `Statutory Auditors`,
  `our Company`, `Promoter Group`, `Non-Banking Financial Company`);
* generic references (`the Company`, `Group Entities`, `the Stock Exchanges`).

Defined short forms **are** annotated where they appear: `KSH`, `CARE`,
`CareEdge Research`, `ICICI Securities`.

## ADDRESS

Annotate a postal address: at minimum a locality plus a postal code, normally
with a house/plot number, building, street and state. Start the span at the first
address element and end it at the last (`India`, when present).

* The label (`Registered Office:`) is not part of the span.
* A preceding company name is not part of the span.
* A bare place reference in prose (`our facility in Pune`) is **not** an address.
* A locality-plus-PIN fragment (`Pune – 411 004`) on its own **is** annotated,
  because it is enough to locate a party.

## PHONE

Annotate telephone, mobile and fax numbers including the country code and the
original grouping. Registration numbers, DINs, CINs and PIN codes are not phones.

## EMAIL

Annotate the whole address, local part and domain. Role mailboxes
(`ipo@trilegal.com`) are annotated: the domain identifies the firm.

## NATIONAL_ID

Annotate DIN, CIN, PAN, Aadhaar, GSTIN, SEBI registration numbers, ICAI firm
registration numbers, professional membership numbers, passport and driving
licence numbers. These are annotated because a redaction that removes a name and
leaves a unique key pointing at it is not a redaction.

ISIN, and identifiers of instruments rather than of parties, are not annotated.

## URL

Annotate a website or URL belonging to a redacted party. Do not annotate URLs of
public bodies (`www.sebi.gov.in`, `www.bseindia.com`, `www.nseindia.com`).

## SSN, CREDIT_CARD, DOB, IP_ADDRESS

Annotate wherever they occur. None occurs in this document; these types are
evaluated on the synthetic corpus instead (see `tests/fixtures/synthetic_corpus.jsonl`
and `EVALUATION_REPORT.md`).

A date is a DOB only when a birth cue ties it to a person. Incorporation dates,
board-resolution dates, fiscal period ends and bid dates are not DOBs.

Also annotate **peer review certificate numbers** of audit firms: same argument as
the firm registration number they sit beside.

## Known ambiguity, resolved once here

* **Dates of incorporation and share-acquisition dates** are not PII under this
  guideline. They are transaction facts.
* **Ages** (`aged 72 years`) are quasi-identifiers but are outside the
  assignment's type list and are not annotated.
* **Share counts, amounts and percentages** are never PII.
* **Job titles** are never PII on their own.
* **Newspaper and publication titles** (`Financial Express`, `Jansatta`,
  `Loksatta`) are not annotated. They are named as the statutory advertising
  medium, not as a party, and they identify no individual.
* **An entity name used as a landmark inside an address** (`above HDFC Limited
  Karve Road`) is part of the ADDRESS span and is not separately annotated as an
  ORGANIZATION. The whole line is one value.

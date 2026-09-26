"""Word lists that encode this tool's precision policy.

Every entry here exists because it prevented a concrete false positive or
enabled a concrete true positive while the tool was being built against the
KSH International Red Herring Prospectus. They are grouped so the *reason* for
each list is obvious, because these lists -- not the regexes -- are what a
reviewer needs to audit when precision regresses.

Design note: all matching against these sets is case-insensitive and is done on
a normalised form (collapsed whitespace, curly quotes folded to ASCII). Use
:func:`norm` rather than comparing raw strings.
"""

from __future__ import annotations

import re
import unicodedata

_WS_RE = re.compile(r"\s+")
_PUNCT_FOLD = {
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "–": "-",
    "—": "-",
    "−": "-",
    " ": " ",
}


def norm(text: str) -> str:
    """Casefolded, whitespace-collapsed, punctuation-folded comparison key."""
    text = unicodedata.normalize("NFKC", text)
    for bad, good in _PUNCT_FOLD.items():
        text = text.replace(bad, good)
    return _WS_RE.sub(" ", text).strip().casefold()


def _set(*blocks: str) -> frozenset[str]:
    """Build a normalised lookup set from indented text blocks."""
    out: set[str] = set()
    for block in blocks:
        for line in block.strip().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                out.add(norm(line))
    return frozenset(out)


def _words(*blocks: str) -> frozenset[str]:
    """Build a normalised set of individual whitespace-separated tokens."""
    out: set[str] = set()
    for block in blocks:
        for token in block.split():
            token = token.strip()
            if token and not token.startswith("#"):
                out.add(norm(token))
    return frozenset(out)


# ---------------------------------------------------------------------------
# 1. Public and statutory entities -- NEVER redacted.
# ---------------------------------------------------------------------------
# Policy: an organisation name is treated as sensitive only when it identifies a
# *private* party to the transaction. Regulators, courts, exchanges,
# standard-setters and units of government are matters of public record;
# redacting them would destroy the document's legal meaning without protecting
# anyone. This choice is stated explicitly in the README.
PUBLIC_BODIES = _set(
    """
    Securities and Exchange Board of India
    Reserve Bank of India
    Registrar of Companies
    Ministry of Corporate Affairs
    Ministry of Finance
    Central Board of Direct Taxes
    Income Tax Department
    Government of India
    Central Government
    State Government
    Supreme Court of India
    High Court
    National Company Law Tribunal
    Insurance Regulatory and Development Authority of India
    Pension Fund Regulatory and Development Authority
    Competition Commission of India
    Directorate General of Foreign Trade
    Bureau of Indian Standards
    Central Pollution Control Board
    Maharashtra Pollution Control Board
    Maharashtra Industrial Development Corporation
    Solar Energy Corporation of India
    National Payments Corporation of India
    Securities and Exchange Commission
    U.S. Securities and Exchange Commission
    United States Securities and Exchange Commission
    Financial Benchmarks India Private Limited
    """,
    # Exchanges, depositories and market infrastructure: public utilities of the
    # capital market whose identity is inseparable from the offer's terms.
    """
    BSE Limited
    National Stock Exchange of India Limited
    National Securities Depository Limited
    Central Depository Services (India) Limited
    Central Depository Services Limited
    Stock Exchanges
    Designated Stock Exchange
    """,
    # Standard setters and codified frameworks.
    """
    Institute of Chartered Accountants of India
    International Accounting Standards Board
    Companies Act
    Indian Accounting Standards
    """,
)

#: Public-body acronyms and short forms that must survive even when they look
#: like a company token. Checked as whole tokens.
PUBLIC_BODY_TOKENS = _words(
    """
    SEBI RBI NSE BSE RoC MCA NCLT ICAI NSDL CDSL NPCI IRDAI MIDC SEC
    GoI FEMA FDI ICDR LODR AIF FPI QIB ASBA UPI CIN DIN PAN GST TDS
    NRI OCB FVCI VCF SCSB SCSBs RTA IPO FPO OFS DP KYC AML PMLA
    IFRS GAAP EBITDA
    """
)


# ---------------------------------------------------------------------------
# 2. Legal-entity suffixes -- the anchor for organisation detection.
# ---------------------------------------------------------------------------
# Ordered longest-first so regex alternation prefers "Private Limited" over
# "Limited".
ORG_SUFFIXES: tuple[str, ...] = (
    "Limited Liability Partnership",
    "Public Limited Company",
    "Private Limited",
    "Family Trust",
    "Partnership Firm",
    "Pvt. Ltd.",
    "Pvt Ltd",
    "Corporation",
    "Incorporated",
    "Associates",
    "Enterprises",
    "Industries",
    "Technologies",
    "Securities",
    "Solutions",
    "Ventures",
    "Holdings",
    "Limited",
    "Company",
    "Trust",
    "GmbH",
    "Ltd.",
    "Ltd",
    "LLP",
    "Inc.",
    "Inc",
    "S.A.",
    "N.V.",
    "Co.",
    "Bank",
)

#: Suffixes too generic to anchor on their own -- they only count when the
#: tokens to their left already look like a distinctive proper name.
WEAK_ORG_SUFFIXES = frozenset(
    norm(s)
    for s in ("Company", "Trust", "Bank", "Co.", "Associates", "Securities")
)

#: Phrases that end in an org suffix but name a *role defined by the offer*, not
#: an entity. "Escrow Collection Bank" is a contractual role; the bank filling
#: it ("HDFC Bank Limited") is a separate, redacted mention.
ORG_ROLE_PHRASES = _set(
    """
    Escrow Collection Bank
    Public Offer Account Bank
    Offer Escrow Collection Bank
    Refund Bank
    Sponsor Bank
    Short Term Bank
    Long Term Bank
    Scheduled Commercial Bank
    Self Certified Syndicate Bank
    Syndicate Member
    Book Running Lead Manager
    Book Running Lead Managers
    Lead Manager
    Registrar to the Offer
    Statutory Auditor
    Statutory Auditors
    Peer Review Auditor
    Previous Statutory Auditor
    Independent Chartered Accountant
    Chartered Accountant
    Chartered Accountants
    Chartered Engineer
    Credit Rating Agency
    Legal Counsel
    Domestic Legal Counsel
    International Legal Counsel
    Our Company
    The Company
    Holding Company
    Subsidiary Company
    Group Company
    Group Companies
    Group Entity
    Group Entities
    Associate Company
    Practicing Company Secretary
    Practising Company Secretary
    Company Secretary
    Depositories Act
    Foreign Ownership of Indian Securities
    Promoter Group
    Selling Shareholder
    Selling Shareholders
    Promoter Selling Shareholder
    Promoter Selling Shareholders
    Mutual Fund
    Mutual Funds
    Insurance Company
    Foreign Institutional Investor
    Non-Banking Financial Company
    Systemically Important Non-Banking Financial Company
    Housing Finance Company
    Asset Reconstruction Company
    Core Investment Company
    Alternative Investment Fund
    Venture Capital Fund
    Limited Liability Partnership
    Public Limited Company
    Private Limited
    Family Trust
    Trust
    Bank
    Company
    Limited
    Securities
    Corporation
    Associates
    Industries
    Enterprises
    """
)


# ---------------------------------------------------------------------------
# 3. Person-name support.
# ---------------------------------------------------------------------------
HONORIFICS = _words(
    """
    Mr. Mr Mrs. Mrs Ms. Ms Miss Dr. Dr Shri Smt. Smt Sri Prof. Prof
    CA CS Adv. Adv Justice Late Sh. Sh
    """
)

#: Structural cues that introduce a person's name in a filing of this kind.
#: These yield the high-confidence PERSON hits that seed the gazetteer.
PERSON_CUE_LABELS: tuple[str, ...] = (
    "Company Secretary and Compliance Officer",
    "Chairman and Executive Director",
    "Chairman and Managing Director",
    "Investor Relations Officer",
    "Chief Financial Officer",
    "Joint Managing Director",
    "Non-Executive Director",
    "Independent Director",
    "Authorised Signatory",
    "Compliance Officer",
    "Whole-time Director",
    "Wholetime Director",
    "Executive Director",
    "Company Secretary",
    "Chartered Engineer",
    "Managing Director",
    "Nominee Director",
    "Contact Person",
    "Designation",
    "Director",
    "Promoter",
    "Promoters",
    "Partner",
    "Proprietor",
)

#: Job titles / designations. A capitalised phrase made only of these words is
#: a role, not a person.
DESIGNATION_WORDS = _words(
    """
    chairman chairperson director directors managing joint whole-time wholetime
    executive independent non-executive nominee additional alternate promoter
    promoters secretary compliance officer chief financial accounting operating
    president vice chairman-cum-managing partner proprietor member members
    manager management auditor auditors engineer signatory relations investor
    shareholder shareholders trustee trustees designate designation and cum
    """
)

#: Tokens that make a capitalised phrase an *address component* rather than a
#: person's name. spaCy tags "Village Birdewadi" and "Bandra Kurla Complex" as
#: PERSON; these words veto that.
ADDRESS_COMPONENT_WORDS = _words(
    """
    village taluka tal tehsil district dist gram panchayat post mauje
    road roads rd street st lane marg marga path chowk cross corner
    nagar nager puram pura pur wadi gaon peth ganj
    sector block plot survey gat khasra cts
    floor flr basement wing tower towers annexe annex
    apartment apartments apt flat flats building bldg bunglow bungalow
    house chambers chamber complex centre center plaza arcade mall
    society co-operative cooperative chs colony park parks garden gardens
    residency residence enclave estate estates layout phase zone
    industrial midc area premises campus
    railway station bus depot airport port jetty
    east west north south central near behind opposite beside adjacent
    above below front rear off
    hall hotel court gymkhana ground grounds maidan basketball reclamation
    pin pincode postal
    """
)

#: Indian states / union territories -- an address tail marker, never a person.
INDIAN_STATES = _set(
    """
    Andhra Pradesh
    Arunachal Pradesh
    Assam
    Bihar
    Chhattisgarh
    Goa
    Gujarat
    Haryana
    Himachal Pradesh
    Jharkhand
    Karnataka
    Kerala
    Madhya Pradesh
    Maharashtra
    Manipur
    Meghalaya
    Mizoram
    Nagaland
    Odisha
    Orissa
    Punjab
    Rajasthan
    Sikkim
    Tamil Nadu
    Telangana
    Tripura
    Uttar Pradesh
    Uttarakhand
    West Bengal
    Andaman and Nicobar Islands
    Chandigarh
    Dadra and Nagar Haveli
    Daman and Diu
    Delhi
    New Delhi
    Jammu and Kashmir
    Ladakh
    Lakshadweep
    Puducherry
    """
)

#: Cities, suburbs and districts that appear as the locality element of an
#: address. Used by the address detector to recognise the "<city> - <PIN>" tail
#: and by the person detector as a veto.
KNOWN_LOCALITIES = _set(
    """
    Mumbai
    Navi Mumbai
    Pune
    Bengaluru
    Bangalore
    Chennai
    Kolkata
    Hyderabad
    Ahmedabad
    Delhi
    Noida
    Gurugram
    Gurgaon
    Jaipur
    Lucknow
    Kanpur
    Nagpur
    Indore
    Bhopal
    Surat
    Vadodara
    Baroda
    Rajkot
    Nashik
    Nasik
    Aurangabad
    Kolhapur
    Solapur
    Thane
    Raigad
    Panvel
    Taloja
    Chakan
    Khed
    Akurdi
    Pimpri
    Chinchwad
    Ahmednagar
    Ahilyanagar
    Parner
    Supa
    Birdewadi
    Khalumbre
    Padghe
    Savli
    Baner
    Kothrud
    Erandawane
    Shivajinagar
    Shivaji Nagar
    Deccan Gymkhana
    Koregaon Park
    Bund Garden
    Prabhat Road
    Model Colony
    Pashan
    Panchvati
    Bandra
    Bandra East
    Bandra Kurla Complex
    Vikhroli
    Kanjurmarg
    Prabhadevi
    Churchgate
    Lower Parel
    Worli
    Andheri
    Goregaon
    Powai
    Chembur
    Byculla
    Colaba
    Fort
    Govindpura
    Huzur
    Minal Residency
    Abhimanshree Society
    India
    Maharashtra
    """
)

#: Ordinary-English and document-jargon capitalised phrases that spaCy's NER
#: repeatedly mislabels as PERSON or ORG in this corpus.
NON_PERSON_PHRASES = _set(
    """
    S. No.
    S. No
    S No
    S. N o.
    S. N
    Sr. No.
    Sl. No.
    Serial No.
    Cap Price
    Floor Price
    Offer Price
    Issue Price
    Bid Price
    Bid
    Bids
    Bidder
    Bidders
    Offer
    Offers
    Issue
    Issuer
    Fiscal
    Fiscals
    Fiscal Year
    Financial Year
    Red Herring
    Red Herring Prospectus
    Draft Red Herring Prospectus
    Prospectus
    Equity Share
    Equity Shares
    Preference Shares
    Net Proceeds
    Gross Proceeds
    Offer Proceeds
    Anchor Investor
    Anchor Investors
    Retail Individual Investor
    Retail Individual Investors
    Non-Institutional Investor
    Non-Institutional Investors
    Qualified Institutional Buyer
    Qualified Institutional Buyers
    Eligible Employee
    Eligible Employees
    Working Day
    Working Days
    Board
    Board of Directors
    Audit Committee
    Memorandum of Association
    Articles of Association
    Restated Financial Statements
    Summary Financial Statements
    Risk Factors
    Our Business
    Our Management
    Our Promoters
    Capital Structure
    Basis for the Offer Price
    Objects of the Offer
    Material Contracts
    General Information
    Other Regulatory and Statutory Disclosures
    Statement of Special Tax Benefits
    Industry Overview
    Management Discussion and Analysis
    Definitions and Abbreviations
    Forward-Looking Statements
    Table of Contents
    Section
    Sections
    Chapter
    Annexure
    Schedule
    Unpaid
    Unpai
    Gift
    Gifts
    Corporate Office
    Registered Office
    Corporate Identity Number
    Materiality Policy
    Related Party Transactions
    Credit Rating
    Letter of Offer
    Offer Agreement
    Syndicate Agreement
    Underwriting Agreement
    Share Escrow Agreement
    Cash Escrow and Sponsor Bank Agreement
    Registrar Agreement
    Book Building Process
    Price Band
    Bid Lot
    Allotment Advice
    Allotment
    Allottee
    Allottees
    Designated Date
    Designated Stock Exchange
    Bid Offer Period
    Offer Period
    Weighted Average Cost of Acquisition
    Return on Equity
    Return on Capital Employed
    Net Debt
    Non-GAAP
    Magnet Winding Wires
    Winding Wire
    Copper Wire
    Power Sector
    Railways Sector
    Renewable Energy
    Supa Facility
    Chakan Facility
    Unit
    Units
    Plant
    Rupees
    Indian Rupees
    Lakh
    Lakhs
    Crore
    Crores
    Million
    Billion
    Monday
    Tuesday
    Wednesday
    Thursday
    Friday
    Saturday
    Sunday
    January
    February
    March
    April
    May
    June
    July
    August
    September
    October
    November
    December
    """
)

#: Generic commercial vocabulary. A capitalised phrase built only from these is a
#: column label or a line item ("TOTAL OFFER SIZE", "Bid Lot Details"), never a
#: person or a private entity. This is the veto that stops the table-column
#: strategies from firing on the layout tables of a cover page.
GENERIC_BUSINESS_WORDS = _words(
    """
    total subtotal grand net gross aggregate amount amounts value values
    size sizes number numbers count quantity qty details detail particulars
    description descriptions remarks notes note reference ref
    price prices band bands lot lots range ranges high low average weighted
    date dates day days month months year years period periods quarter
    opening closing open close start end from to upto up
    offer offers issue issues bid bids ask allotment allocation portion
    share shares equity preference capital structure paid unpaid premium
    ownership owner owners holding holdings shareholding stake stakes
    face nominal book building process discount rebate cash cheque
    percentage percent ratio ratios margin margins revenue income profit
    loss expense expenses cost costs asset assets liability liabilities
    cashflow turnover sales purchase purchases tax taxes duty duties
    statement statements schedule schedules annexure annexures
    saturday saturdays sunday sundays holiday holidays weekday weekdays
    fee fees charge charges interest dividend yield return returns
    category categories type types class classes series group groups
    status stage level tier band code codes id ids serial
    name names title titles designation designations address addresses
    contact contacts telephone phone email website web url
    company companies firm firms entity entities party parties
    investor investors shareholder shareholders holder holders
    promoter promoters director directors officer officers member members
    employee employees customer customers supplier suppliers vendor vendors
    bank banks account accounts branch branches
    fiscal financial statutory regulatory legal compliance
    india indian domestic international global foreign
    limited private public listed unlisted
    total.
    """
)

#: Tokens that disqualify a capitalised phrase from being a person's name, over
#: and above the address/designation/locality vetoes. Three groups, each of which
#: produced a real false positive on this corpus:
#:
#: * **financial acronyms** -- "PAT CAGR" was reported as a person;
#: * **legal-instrument words** -- so was "Depositories Act";
#: * **structural words and Roman numerals** -- so was "SECTION III".
NON_PERSON_TOKENS = _words(
    """
    cagr pat pbt pbdt ebit ebitda roe roce roa roic eps nav npa dso dpo dio
    capex opex fcf gmv arpu yoy qoq mom ttm ytd fy h1 h2 q1 q2 q3 q4
    pe pb ev sga cogs wacc irr npv mtm ltv cac gst tds tcs
    act acts rule rules regulation regulations code codes bill ordinance
    notification circular guidelines policy policies scheme schemes
    agreement agreements deed deeds memorandum articles resolution resolutions
    statute statutes amendment amendments provision provisions
    section sections chapter chapters annexure annexures schedule schedules
    part parts clause clauses para paragraph paragraphs exhibit appendix
    i ii iii iv v vi vii viii ix x xi xii xiii xiv xv xvi xvii xviii xix xx
    """
)

#: Words that mark a name-shaped phrase as a *government scheme* named after a
#: person rather than a reference to that person. "Deen Dayal Upadhyaya Gram
#: Jyoti Yojana" and "Kisan Urja Suraksha evam Utthaan Mahabhiyan" are both in
#: this document, and both look exactly like Indian personal names.
SCHEME_CONTEXT_WORDS = _words(
    """
    yojana yojna scheme abhiyan mahabhiyan mission awas gram jyoti
    suraksha utthaan kalyan vikas bima pariyojana nidhi kosh sampada
    programme program initiative campaign fund grant subsidy
    """
)


# ---------------------------------------------------------------------------
# 4. Numeric-context guards.
# ---------------------------------------------------------------------------
#: Labels meaning a following number is a *registration* identifier, not a phone
#: number or a card number. Prevents the phone detector from eating strings such
#: as "SEBI Registration Number: INM000011419".
REGISTRATION_LABELS: tuple[str, ...] = (
    "SEBI Registration Number",
    "SEBI Registration No",
    "SEBI Registration",
    "Firm Registration Number",
    "Firm registration number",
    "ICAI Firm Registration",
    "Registration Number",
    "Registration No",
    "Peer Review Certificate",
    "Membership Number",
    "Membership No",
    "Certificate of Registration",
    "Corporate Identity Number",
    "Director Identification Number",
    "CIN",
    "DIN",
    "GSTIN",
    "LEI",
    "ISIN",
    "IEC",
)

#: Labels meaning a following number really is a telephone number.
PHONE_LABELS: tuple[str, ...] = (
    "Telephone",
    "Tel.",
    "Tel",
    "Phone",
    "Mobile",
    "Fax",
    "Contact Number",
    "Contact No",
)

#: Labels meaning a following date is a date of birth.
DOB_LABELS: tuple[str, ...] = (
    "Date of Birth",
    "date of birth",
    "D.O.B",
    "DOB",
    "Born on",
    "born on",
    "Date of birth",
)

#: Label prefixes that introduce a postal address.
ADDRESS_LABELS: tuple[str, ...] = (
    "Registered and Corporate Office",
    "Registered Office",
    "Corporate Office",
    "Residential Address",
    "Office Address",
    "Principal Place of Business",
    "Address",
    "having its registered office at",
    "with its registered office at",
    "having its office at",
    "situated at",
    "located at",
)

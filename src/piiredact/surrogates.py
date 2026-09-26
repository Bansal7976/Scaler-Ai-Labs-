"""Surrogate generation: replacing each real value with a believable fake one.

The assignment asks for *fake alternatives* rather than black boxes, which is a
much stronger requirement than it looks. A redacted prospectus is only useful if
it still reads like a prospectus, and that needs four properties that a naive
``faker.name()`` per match does not give:

**1. Consistency.** Every mention of one real entity must map to the *same*
surrogate. "Kushal Subbayya Hegde" appears in the cover page, the board table and
the capital structure; if each becomes a different fake person the document
becomes unreadable and the redaction leaks structure (a reader can count
distinct names). Consistency is keyed on the gazetteer's canonical form, so
aliases resolve to the same identity.

**2. Part-wise consistency for derived forms.** Because the gazetteer knows that
"Hegde" is the surname of "Kushal Subbayya Hegde", the surrogate for the bare
surname is the *surname of that person's surrogate*, not a new name. The same
holds for an organisation's brand acronym: if ``KSH International Limited``
becomes ``Trentwood Alloys Limited``, then ``KSH`` becomes ``Trentwood``.

**3. Cross-type coherence.** ``sarthak.malvadkar@kshinternational.com`` is not
replaced with an unrelated address: the local part is rebuilt from the surrogate
of the person the email names, and the domain from the surrogate of the
organisation that owns it. Likewise ``www.kshinternational.com`` and the
company's own name agree.

**4. Format preservation.** ``+91 22 6807 7100`` becomes ``+91 22 4192 6035`` --
same country code, same grouping, same length. ``KSH INTERNATIONAL LIMITED``
keeps its upper case. An organisation keeps its legal suffix, so a
``Private Limited`` stays a ``Private Limited`` and the document's legal sense
survives.

Safety properties: generation is deterministic given ``--seed`` (so a run is
reproducible and reviewable), a surrogate is never equal to the value it
replaces, emails and URLs use the RFC 2606 reserved domains, and IP addresses use
the RFC 5737 / RFC 3849 documentation ranges. Generated Aadhaar numbers carry a
*valid* Verhoeff check digit and generated card numbers are Luhn-valid, so that
downstream validators still accept the redacted document; the README notes the
one consequence worth knowing about, which is that a format-preserving fake phone
number could coincidentally match a real subscriber.
"""

from __future__ import annotations

import hashlib
import random
import re
import string
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Iterable

from faker import Faker

from .detectors.base import Gazetteer
from .detectors.person import derive_short_forms
from .lexicons import INDIAN_STATES, ORG_SUFFIXES, norm
from .types import PIIType, Span

# RFC 2606 reserved names: safe to publish, can never route anywhere real.
RESERVED_EMAIL_DOMAIN = "example.com"
RESERVED_DOMAIN_TLD = "example"
#: RFC 5737 documentation-only IPv4 blocks.
_DOC_IPV4_BLOCKS = ("192.0.2", "198.51.100", "203.0.113")
#: RFC 3849 documentation-only IPv6 prefix.
_DOC_IPV6_PREFIX = "2001:db8"

#: Company-name word pools. Kept local rather than taken from Faker because the
#: surrogate must preserve the *register* of an Indian industrial filing, and
#: Faker's ``company()`` produces "Vala Group"-style names that do not.
_ORG_FIRST_WORDS = (
    "Trentwood", "Northmoor", "Alderwick", "Brightvale", "Calderstone",
    "Eastmere", "Fairholt", "Granthorpe", "Havenbrook", "Ironvale",
    "Kestrelton", "Larkfield", "Marlowe", "Netherby", "Oakhurst",
    "Pinecrest", "Quarryhill", "Rivermead", "Stonebridge", "Thornleigh",
    "Uplandia", "Vantage", "Westhaven", "Yarrowdale", "Zephyrline",
    "Aravind", "Bhaskar", "Chandrika", "Devaki", "Ekansh", "Girish",
    "Harita", "Ishaan", "Jagdev", "Kailash", "Lavanya", "Mahadev",
)
_ORG_MIDDLE_WORDS = (
    "Alloys", "Metals", "Conductors", "Cables", "Polymers", "Composites",
    "Industrial", "Precision", "Engineering", "Fabrication", "Foundry",
    "Insulation", "Magnetics", "Winding", "Extrusion", "Logistics",
    "Infrastructure", "Distriparks", "Capital", "Advisory", "Consulting",
    "Analytics", "Financial", "Wealth", "Markets", "Trading", "Projects",
    "Power", "Energy", "Renewables", "Automation", "Systems",
)
#: Fictional mountain names for the trust surrogates, matching the source
#: document's own convention of naming family trusts after Himalayan peaks.
_TRUST_WORDS = (
    "Silverpeak", "Cloudridge", "Stormcrest", "Windhollow", "Frostspire",
    "Sunmantle", "Rainhold", "Moonscarp", "Emberfell", "Glasscairn",
)

#: Localities and street stems for address surrogates, so the fake address reads
#: like an Indian one without reusing a real place name from the document.
_FAKE_LOCALITIES = (
    "Sundarpeth", "Navrangwadi", "Hirapur", "Jalvihar", "Kalpataru Nagar",
    "Mohanwadi", "Rajgad Colony", "Shantivan", "Tarunagar", "Vidyapeeth",
    "Anandpura", "Chitrakoot", "Devgiri", "Gokhalewadi", "Indrayani Nagar",
)
_FAKE_CITIES = (
    "Rampur", "Navsari", "Kalyanpur", "Shivgarh", "Meharpur", "Balbhadra",
    "Jamnagar", "Tarapur", "Udaynagar", "Vikrampur",
)
_FAKE_STREET_STEMS = (
    "Ashok", "Bhagat", "Chandra", "Dhruv", "Ganga", "Hemant", "Indira",
    "Jyoti", "Kamal", "Lakshmi", "Meera", "Nandan", "Prakash", "Ratna",
    "Sagar", "Tulsi", "Vasant", "Yamuna",
)
_STREET_KINDS = ("Road", "Marg", "Path", "Lane", "Cross Road", "Avenue")
_BUILDING_KINDS = (
    "Apartments", "Residency", "Heights", "Enclave", "Society", "Complex",
    "Towers", "Plaza", "Chambers", "House",
)

#: Email local-part tokens that may be kept verbatim: they name a function, not
#: a person. Anything else is substituted, including tokens the engine could not
#: link to a known entity, so that no fragment of a real name survives.
_KEEPABLE_LOCAL_TOKENS = frozenset(
    {
        "info", "contact", "support", "help", "helpdesk", "admin", "office",
        "sales", "enquiry", "enquiries", "inquiry", "queries", "query",
        "ipo", "ipos", "investor", "investors", "ir", "relations",
        "customercare", "customerservice", "customer", "care", "service",
        "compliance", "secretarial", "cs", "grievance", "grievances",
        "complaints", "redressal", "legal", "pro", "press", "media",
        "hr", "careers", "jobs", "noreply", "no", "reply", "webmaster",
        "mail", "email", "general", "corp", "corporate", "connect",
        "mb", "in", "co", "rm", "rm6", "cmg", "desk", "team", "group",
    }
)

_ORG_SUFFIX_MATCH = re.compile(
    "(?:" + "|".join(re.escape(s) for s in sorted(ORG_SUFFIXES, key=len, reverse=True)) + ")$",
    re.IGNORECASE,
)
_DIGIT_RE = re.compile(r"\d")
_MONTHS = (
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
)


# ---------------------------------------------------------------------------
# Case and format helpers
# ---------------------------------------------------------------------------
def detect_case_style(text: str) -> str:
    """``"upper"``, ``"lower"``, ``"title"`` or ``"mixed"``."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "mixed"
    if all(c.isupper() for c in letters):
        return "upper"
    if all(c.islower() for c in letters):
        return "lower"
    words = [w for w in re.split(r"\s+", text) if w and w[0].isalpha()]
    if words and all(w[0].isupper() for w in words):
        return "title"
    return "mixed"


def apply_case_style(text: str, style: str) -> str:
    if style == "upper":
        return text.upper()
    if style == "lower":
        return text.lower()
    return text


def substitute_digits(template: str, digits: Iterable[str]) -> str:
    """Rewrite ``template`` keeping every non-digit character in place."""
    iterator = iter(digits)
    out = []
    for char in template:
        if char.isdigit():
            out.append(next(iterator, "0"))
        else:
            out.append(char)
    return "".join(out)


# ---------------------------------------------------------------------------
# Checksums for the identifiers we generate
# ---------------------------------------------------------------------------
def _verhoeff_check_digit(payload: str) -> str:
    """The Verhoeff check digit for an 11-digit Aadhaar payload.

    Found by trying all ten digits against the *same* validator the detector
    uses, rather than by inverting the multiplication table. Ten iterations is
    free, and it makes the generator and the validator impossible to disagree --
    an inverse-table version of this silently produced numbers the detector
    rejected.
    """
    from .detectors.national_id import verhoeff_ok

    for digit in "0123456789":
        if verhoeff_ok(payload + digit):
            return digit
    raise AssertionError(f"no valid Verhoeff check digit for {payload!r}")


def _luhn_check_digit(payload: str) -> str:
    total = 0
    for index, char in enumerate(reversed(payload)):
        value = int(char)
        if index % 2 == 0:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return str((10 - total % 10) % 10)


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------
@dataclass
class SurrogateEngine:
    """Deterministic, consistent fake-value generator.

    Parameters
    ----------
    gazetteer:
        Supplies canonical forms and alias links, so that every mention of one
        entity resolves to one surrogate and derived forms borrow the matching
        part of it.
    seed:
        Any string. The same seed and the same document always produce the same
        surrogates, which is what makes a run auditable.
    locale:
        Faker locale used for person names and place words.
    """

    gazetteer: Gazetteer
    seed: str = "piiredact"
    locale: str = "en_IN"

    #: canonical key -> surrogate, per PII type
    _cache: dict[tuple[PIIType, str], str] = field(default_factory=dict, init=False)
    #: canonical key -> surrogate token list, for part-wise derivation
    _person_tokens: dict[str, list[str]] = field(default_factory=dict, init=False)
    _org_tokens: dict[str, list[str]] = field(default_factory=dict, init=False)
    #: surrogates already handed out, so two entities never collide
    _issued: set[str] = field(default_factory=set, init=False)
    #: leading words already used for an organisation. Two companies sharing a
    #: first word ("Stonebridge Alloys" and "Stonebridge Bank") read as related
    #: entities to a reviewer, which is a relationship the original may not have.
    _issued_heads: set[str] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        self._faker = Faker(self.locale)
        self._generators: dict[PIIType, Callable[[str, random.Random], str]] = {
            PIIType.PERSON: self._make_person,
            PIIType.EMAIL: self._make_email,
            PIIType.PHONE: self._make_phone,
            PIIType.ORGANIZATION: self._make_organization,
            PIIType.ADDRESS: self._make_address,
            PIIType.SSN: self._make_ssn,
            PIIType.CREDIT_CARD: self._make_credit_card,
            PIIType.DOB: self._make_dob,
            PIIType.IP_ADDRESS: self._make_ip,
            PIIType.NATIONAL_ID: self._make_national_id,
            PIIType.URL: self._make_url,
        }

    # -- public API ----------------------------------------------------------

    def surrogate_for(self, span: Span) -> str:
        """The replacement text for one resolved span."""
        canonical_key = self.gazetteer.canonical_key(span.text) or norm(span.text)
        cache_key = (span.pii_type, canonical_key)

        # Derived forms (a bare surname, a brand acronym) borrow from the parent.
        derived = self._derived_surrogate(span, canonical_key)
        if derived is not None:
            return self._match_case(span.text, derived)

        if cache_key not in self._cache:
            rng = self._rng(span.pii_type, canonical_key)
            canonical_text = self.gazetteer.canonical_text(span.text) or span.text
            generator = self._generators.get(span.pii_type)
            if generator is None:  # pragma: no cover - every type has one
                raise KeyError(f"no surrogate generator for {span.pii_type}")
            value = self._unique(
                generator,
                canonical_text,
                rng,
                enforce_uniqueness=span.pii_type in self._UNIQUE_TYPES,
            )
            self._cache[cache_key] = value
        return self._match_case(span.text, self._cache[cache_key])

    def mapping(self) -> list[dict[str, str]]:
        """The full real-to-fake table, for the audit log."""
        rows = []
        for (pii_type, key), value in sorted(
            self._cache.items(), key=lambda item: (str(item[0][0]), item[0][1])
        ):
            entry = self.gazetteer.entries.get(key)
            rows.append(
                {
                    "pii_type": str(pii_type),
                    "original": entry[0] if entry else key,
                    "surrogate": value,
                }
            )
        return rows

    # -- internals -----------------------------------------------------------

    def _rng(self, pii_type: PIIType, key: str) -> random.Random:
        digest = hashlib.blake2b(
            f"{self.seed}\x00{pii_type}\x00{key}".encode("utf-8"), digest_size=16
        ).digest()
        return random.Random(int.from_bytes(digest, "big"))

    #: Types where two distinct entities must never share a surrogate, because a
    #: reader would merge them. Emails and URLs are excluded on purpose: two
    #: addresses at one company *should* share a domain, and forcing them apart
    #: produced "www.kailash.example 45".
    _UNIQUE_TYPES = frozenset(
        {PIIType.PERSON, PIIType.ORGANIZATION, PIIType.ADDRESS}
    )

    def _unique(
        self,
        generator: Callable[[str, random.Random], str],
        original: str,
        rng: random.Random,
        *,
        enforce_uniqueness: bool = True,
    ) -> str:
        """Generate until the surrogate differs from the original and is new."""
        for _ in range(64):
            candidate = generator(original, rng).strip()
            if not candidate:
                continue
            if norm(candidate) == norm(original):
                continue
            if enforce_uniqueness and candidate in self._issued:
                continue
            self._issued.add(candidate)
            return candidate
        # Exhausted: append a disambiguating suffix rather than risk a collision.
        fallback = f"{generator(original, rng).strip()} {rng.randint(2, 99)}"
        self._issued.add(fallback)
        return fallback

    @staticmethod
    def _match_case(original: str, surrogate: str) -> str:
        return apply_case_style(surrogate, detect_case_style(original))

    def _derived_surrogate(self, span: Span, canonical_key: str) -> str | None:
        """Part-wise surrogate for a contraction of a known entity."""
        if span.pii_type not in (PIIType.PERSON, PIIType.ORGANIZATION):
            return None
        own_key = norm(span.text)
        if own_key == canonical_key:
            return None
        parent = self.gazetteer.entries.get(canonical_key)
        if parent is None or parent[1] is not span.pii_type:
            return None
        # Force the parent's surrogate to exist first.
        parent_surrogate = self._cache.get((span.pii_type, canonical_key))
        if parent_surrogate is None:
            rng = self._rng(span.pii_type, canonical_key)
            generator = self._generators[span.pii_type]
            parent_surrogate = self._unique(generator, parent[0], rng)
            self._cache[(span.pii_type, canonical_key)] = parent_surrogate
        mapped = self._map_tokens(parent[0], parent_surrogate, span.text)
        if mapped:
            self._cache.setdefault((span.pii_type, own_key), mapped)
            return mapped
        return None

    @staticmethod
    def _map_tokens(real_full: str, fake_full: str, real_part: str) -> str | None:
        """Take the tokens of ``fake_full`` that sit where ``real_part`` sits.

        ``("Kushal Subbayya Hegde", "Aarav Deshmukh Iyer", "Hegde") -> "Iyer"``
        ``("KSH International Limited", "Trentwood Alloys Limited", "KSH")
        -> "Trentwood"``
        """
        real_tokens = real_full.split()
        fake_tokens = fake_full.split()
        part_tokens = real_part.split()
        if not part_tokens or len(real_tokens) != len(fake_tokens):
            return None
        keys = [norm(t) for t in real_tokens]
        picked: list[str] = []
        for token in part_tokens:
            key = norm(token)
            if key not in keys:
                return None
            picked.append(fake_tokens[keys.index(key)])
        return " ".join(picked)

    # -- per-type generators -------------------------------------------------

    def _make_person(self, original: str, rng: random.Random) -> str:
        self._faker.seed_instance(rng.getrandbits(48))
        tokens = original.split()
        # Preserve the shape: how many name parts, and which were initials.
        parts: list[str] = []
        for index, token in enumerate(tokens):
            if re.fullmatch(r"[A-Za-z]\.?", token):
                parts.append(f"{rng.choice(string.ascii_uppercase)}.")
            elif index == 0:
                parts.append(self._faker.first_name())
            elif index == len(tokens) - 1:
                parts.append(self._faker.last_name())
            else:
                parts.append(self._faker.last_name())
        surrogate = " ".join(parts) if parts else self._faker.name()
        self._person_tokens[norm(original)] = surrogate.split()
        return surrogate

    def _make_organization(self, original: str, rng: random.Random) -> str:
        suffix_match = _ORG_SUFFIX_MATCH.search(original)
        suffix = suffix_match.group(0) if suffix_match else ""
        stem_length = len(original) - len(suffix)
        # Store the suffix in its canonical Title-Case spelling. The canonical
        # form of an entity may be its ALL-CAPS cover-page mention, and carrying
        # "FAMILY TRUST" through would produce "Windhollow FAMILY TRUST" in the
        # body text. Per-mention casing is applied later by ``_match_case``.
        if suffix:
            suffix = next(
                (s for s in ORG_SUFFIXES if norm(s) == norm(suffix)), suffix.title()
            )
        stem_tokens = original[:stem_length].split()
        target = max(1, len(stem_tokens))
        if norm(suffix) == "family trust":
            words = [rng.choice(_TRUST_WORDS)]
        else:
            head = next(
                (
                    candidate
                    for candidate in (rng.choice(_ORG_FIRST_WORDS) for _ in range(64))
                    if norm(candidate) not in self._issued_heads
                ),
                rng.choice(_ORG_FIRST_WORDS),
            )
            self._issued_heads.add(norm(head))
            words = [head]
            while len(words) < target:
                choice = rng.choice(_ORG_MIDDLE_WORDS)
                if choice not in words:
                    words.append(choice)
        # Preserve a Roman-numeral qualifier such as "Park VI".
        for token in stem_tokens:
            if re.fullmatch(r"\(?[IVXLC]{1,6}\)?", token) and token not in words:
                words.append(token)
        surrogate = " ".join(words[:target] if target > 1 else words[:1])
        if suffix:
            surrogate = f"{surrogate} {suffix}".strip()
        self._org_tokens[norm(original)] = surrogate.split()
        return surrogate

    def _org_for_domain_stem(self, stem: str) -> tuple[str, tuple[str, PIIType]] | None:
        """Find the organisation a domain label belongs to.

        A domain label has the entity's name with the spaces taken out, so
        ``kshinternational`` never matches the gazetteer key
        ``ksh international limited`` directly. Comparing squashed forms recovers
        the link; without it a company's website got a surrogate domain unrelated
        to the company's own surrogate name.
        """
        squashed_stem = re.sub(r"[^a-z0-9]", "", stem.lower())
        if not squashed_stem:
            return None
        best: tuple[str, tuple[str, PIIType]] | None = None
        best_length = 0
        for key, entry in self.gazetteer.entries.items():
            if entry[1] is not PIIType.ORGANIZATION:
                continue
            squashed = re.sub(r"[^a-z0-9]", "", norm(entry[0]))
            if not squashed:
                continue
            if squashed.startswith(squashed_stem) or squashed_stem.startswith(squashed):
                overlap = min(len(squashed), len(squashed_stem))
                if overlap > best_length:
                    best, best_length = (key, entry), overlap
        return best

    def _org_domain_for(self, host: str, rng: random.Random) -> str:
        """A reserved-TLD domain, reusing the surrogate org name where known."""
        stem = host.lower().removeprefix("www.").split(".")[0]
        key = self.gazetteer.canonical_key(stem)
        entry = self.gazetteer.entries.get(key)
        if entry is None or entry[1] is not PIIType.ORGANIZATION:
            found = self._org_for_domain_stem(stem)
            if found is not None:
                key, entry = found
        if entry and entry[1] is PIIType.ORGANIZATION:
            surrogate = self._cache.get((PIIType.ORGANIZATION, key))
            if surrogate is None:
                surrogate = self._unique(
                    self._make_organization, entry[0], self._rng(PIIType.ORGANIZATION, key)
                )
                self._cache[(PIIType.ORGANIZATION, key)] = surrogate
            label = re.sub(r"[^a-z0-9]", "", surrogate.split()[0].lower())
            return f"{label}.{RESERVED_DOMAIN_TLD}"
        return f"{rng.choice(_ORG_FIRST_WORDS).lower()}.{RESERVED_DOMAIN_TLD}"

    def _make_email(self, original: str, rng: random.Random) -> str:
        local, _, domain = original.partition("@")
        # Rebuild the local part token by token: a token naming a known entity is
        # replaced by that entity's surrogate token; anything else (a role word
        # such as "ipo" or "customercare") is kept, since it carries no identity.
        pieces = re.split(r"([._\-+])", local)
        rebuilt: list[str] = []
        for piece in pieces:
            if piece in "._-+" or not piece:
                rebuilt.append(piece)
                continue
            replacement = self._surrogate_token_for(piece)
            if replacement:
                rebuilt.append(replacement)
            elif norm(piece) in _KEEPABLE_LOCAL_TOKENS or piece.isdigit():
                # A role or structural token ("ipo", "customercare", "mb", "2")
                # carries no identity, so keeping it preserves the mailbox's
                # meaning without leaking anything.
                rebuilt.append(piece.lower())
            else:
                # An unrecognised token may still be a name we failed to link
                # ("ashishmp", "hingnetare"). Substituting keeps the guarantee
                # that no original identifier survives.
                rebuilt.append(self._opaque_token(piece, rng))
        new_local = "".join(rebuilt).strip("._-+").lower() or "contact"
        # Personal addresses get the reserved example.com; corporate addresses get
        # the surrogate company's reserved domain, preserving the real structure.
        stem = domain.split(".")[0].lower()
        if self._names_known_entity(stem):
            new_domain = self._org_domain_for(domain, rng)
        else:
            new_domain = RESERVED_EMAIL_DOMAIN
        return f"{new_local}@{new_domain}"

    def _opaque_token(self, token: str, rng: random.Random) -> str:
        """A deterministic, meaningless replacement for one local-part token."""
        local_rng = random.Random(
            hashlib.blake2b(
                f"{self.seed}\x00local\x00{norm(token)}".encode("utf-8"), digest_size=8
            ).digest()
        )
        self._faker.seed_instance(local_rng.getrandbits(48))
        return re.sub(r"[^a-z]", "", self._faker.last_name().lower()) or "contact"

    def _surrogate_token_for(self, token: str) -> str | None:
        """Surrogate for a single word, when it names a known entity."""
        key = self.gazetteer.canonical_key(token)
        entry = self.gazetteer.entries.get(key)
        if entry is None:
            # Could be one token of a multi-token name (e.g. "malvadkar").
            for cand_key, (surface, pii_type) in self.gazetteer.entries.items():
                if pii_type is not PIIType.PERSON:
                    continue
                if norm(token) in {norm(t) for t in surface.split()}:
                    key, entry = cand_key, (surface, pii_type)
                    break
        if entry is None:
            return None
        surface, pii_type = entry
        if pii_type not in (PIIType.PERSON, PIIType.ORGANIZATION):
            return None
        surrogate = self._cache.get((pii_type, key))
        if surrogate is None:
            surrogate = self._unique(
                self._generators[pii_type], surface, self._rng(pii_type, key)
            )
            self._cache[(pii_type, key)] = surrogate
        mapped = self._map_tokens(surface, surrogate, token)
        if mapped:
            return re.sub(r"[^A-Za-z0-9]", "", mapped).lower()
        # Token position unknown: fall back to the matching-index token.
        real_tokens = [norm(t) for t in surface.split()]
        fake_tokens = surrogate.split()
        if norm(token) in real_tokens and len(real_tokens) == len(fake_tokens):
            return re.sub(
                r"[^A-Za-z0-9]", "", fake_tokens[real_tokens.index(norm(token))]
            ).lower()
        return re.sub(r"[^A-Za-z0-9]", "", fake_tokens[0]).lower()

    def _names_known_entity(self, stem: str) -> bool:
        key = self.gazetteer.canonical_key(stem)
        entry = self.gazetteer.entries.get(key)
        return bool(entry and entry[1] is PIIType.ORGANIZATION)

    def _make_phone(self, original: str, rng: random.Random) -> str:
        """Keep the country code, the separators and the length; change the rest."""
        digits = _DIGIT_RE.findall(original)
        if not digits:
            return original
        keep = 0
        stripped = original.strip()
        if stripped.startswith("+"):
            # Preserve the country code so the number still reads as Indian etc.
            match = re.match(r"\+\s?(\d{1,3})", stripped)
            if match:
                keep = len(match.group(1))
        # Subscriber digits: first one is 6-9 for an Indian mobile/landline area.
        new_digits = list(digits[:keep])
        remaining = len(digits) - keep
        if remaining > 0:
            new_digits.append(str(rng.randint(2, 9)))
            new_digits.extend(str(rng.randint(0, 9)) for _ in range(remaining - 1))
        return substitute_digits(original, new_digits)

    def _make_address(self, original: str, rng: random.Random) -> str:
        """Build a fake address with the same shape as the original.

        The number of comma-separated elements, the presence of a PIN code, a
        state and a trailing "India" are all preserved, so a table column of
        addresses still lines up and still looks like an address.
        """
        elements = [e.strip() for e in re.split(r",", original) if e.strip()]
        has_pin = bool(re.search(r"[1-9]\d{2}\s?\d{3}\b", original))
        pin_spaced = bool(re.search(r"[1-9]\d{2}\s\d{3}\b", original))
        has_india = bool(re.search(r"\bindia\b", original, re.IGNORECASE))
        state = next(
            (
                s
                for s in sorted(INDIAN_STATES, key=len, reverse=True)
                if s in norm(original)
            ),
            None,
        )
        dash = "–" if "–" in original else "-"

        parts: list[str] = []
        target = max(2, len(elements))
        # Element 1: house/plot number plus building.
        parts.append(
            f"{rng.randint(1, 499)}, {rng.choice(_FAKE_STREET_STEMS)} "
            f"{rng.choice(_BUILDING_KINDS)}"
        )
        if target >= 3:
            parts.append(f"{rng.choice(_FAKE_STREET_STEMS)} {rng.choice(_STREET_KINDS)}")
        if target >= 4:
            parts.append(rng.choice(_FAKE_LOCALITIES))
        while len(parts) < target - (1 if has_pin else 0) - (1 if has_india else 0):
            extra = rng.choice(_FAKE_LOCALITIES)
            if extra in parts:
                break
            parts.append(extra)

        city = rng.choice(_FAKE_CITIES)
        if has_pin:
            pin = f"{rng.randint(1, 8)}{rng.randint(10, 99)}"
            tail = f"{rng.randint(0, 9)}{rng.randint(10, 99)}"
            pin_text = f"{pin} {tail}" if pin_spaced else f"{pin}{tail}"
            parts.append(f"{city} {dash} {pin_text}")
        else:
            parts.append(city)
        if state:
            fake_state = rng.choice(
                [s for s in ("Maharashtra", "Gujarat", "Karnataka", "Telangana",
                             "Rajasthan", "Madhya Pradesh") if norm(s) != state]
            )
            parts.append(fake_state)
        if has_india:
            parts.append("India")
        return ", ".join(parts)

    def _make_ssn(self, original: str, rng: random.Random) -> str:
        area = rng.randint(100, 665)
        if area == 666:  # pragma: no cover - excluded by the range
            area = 665
        digits = f"{area:03d}{rng.randint(1, 99):02d}{rng.randint(1, 9999):04d}"
        return substitute_digits(original, digits)

    def _make_credit_card(self, original: str, rng: random.Random) -> str:
        digits = _DIGIT_RE.findall(original)
        length = len(digits)
        # Keep the issuer prefix so the scheme (and any downstream BIN check)
        # still matches, and recompute a valid Luhn check digit.
        prefix = "".join(digits[: 2 if length != 15 else 2])
        body = prefix + "".join(str(rng.randint(0, 9)) for _ in range(length - len(prefix) - 1))
        return substitute_digits(original, body + _luhn_check_digit(body))

    def _make_dob(self, original: str, rng: random.Random) -> str:
        # Shift the date by a pseudo-random amount and re-render in the same
        # format, so "August 4, 1953" stays a long-form date.
        base = date(1950, 1, 1) + timedelta(days=rng.randint(0, 365 * 50))
        if re.search(r"[A-Za-z]{3}", original):
            month = _MONTHS[base.month - 1]
            if re.match(r"^\s*\d", original):  # "4 August 1953"
                return f"{base.day} {month} {base.year}"
            if "," in original:  # "August 4, 1953"
                return f"{month} {base.day}, {base.year}"
            return f"{month} {base.day} {base.year}"
        separator = next((c for c in original if c in "/-."), "/")
        if re.match(r"^\d{4}", original):
            return f"{base.year}{separator}{base.month:02d}{separator}{base.day:02d}"
        return f"{base.day:02d}{separator}{base.month:02d}{separator}{base.year}"

    def _make_ip(self, original: str, rng: random.Random) -> str:
        if ":" in original:
            groups = ":".join(f"{rng.randint(0, 0xFFFF):x}" for _ in range(3))
            return f"{_DOC_IPV6_PREFIX}:{groups}"
        block = rng.choice(_DOC_IPV4_BLOCKS)
        return f"{block}.{rng.randint(1, 254)}"

    def _make_national_id(self, original: str, rng: random.Random) -> str:
        stripped = original.strip()
        # Aadhaar: keep a valid Verhoeff check digit so validators still pass.
        if re.fullmatch(r"[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}", stripped):
            payload = str(rng.randint(2, 9)) + "".join(
                str(rng.randint(0, 9)) for _ in range(10)
            )
            return substitute_digits(original, payload + _verhoeff_check_digit(payload))
        # CIN: <U|L><5 digits><2-letter state><4-digit year><3-letter class><6 digits>
        if re.fullmatch(r"[ULul]\d{5}[A-Za-z]{2}\d{4}[A-Za-z]{3}\d{6}", stripped):
            letters = lambda n: "".join(rng.choice(string.ascii_uppercase) for _ in range(n))
            built = (
                f"{stripped[0]}{rng.randint(10000, 99999)}{letters(2)}"
                f"{rng.randint(1970, 2024)}{letters(3)}{rng.randint(100000, 999999)}"
            )
            return built.upper() if stripped.isupper() else built
        # PAN, GSTIN, SEBI registration, DIN and the rest: preserve the shape
        # character class by character class.
        out = []
        for char in stripped:
            if char.isdigit():
                out.append(str(rng.randint(0, 9)))
            elif char.isalpha():
                letter = rng.choice(string.ascii_uppercase)
                out.append(letter if char.isupper() else letter.lower())
            else:
                out.append(char)
        return "".join(out)

    def _make_url(self, original: str, rng: random.Random) -> str:
        match = re.match(r"^(?P<scheme>\w+://)?(?P<host>[^/?#]+)(?P<rest>.*)$", original)
        if match is None:  # pragma: no cover - URL_RE guarantees a host
            return original
        host = match.group("host")
        prefix = "www." if host.lower().startswith("www.") else ""
        new_host = self._org_domain_for(host, rng)
        return f"{match.group('scheme') or ''}{prefix}{new_host}{match.group('rest')}"


def build_engine(gazetteer: Gazetteer, seed: str, locale: str = "en_IN") -> SurrogateEngine:
    """Factory kept separate so the CLI does not import Faker at module load."""
    return SurrogateEngine(gazetteer=gazetteer, seed=seed, locale=locale)


__all__ = [
    "SurrogateEngine",
    "build_engine",
    "apply_case_style",
    "detect_case_style",
    "substitute_digits",
    "RESERVED_EMAIL_DOMAIN",
]

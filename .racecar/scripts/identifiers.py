#!/usr/bin/env python3
"""Recognise a structured identifier: what kind of thing is this string?

The implementation half of `scripts/identifiers.json`. That file declares every type --
its shape, the algorithm that confirms it, the authority whose rule it is. This module
implements the algorithms that file names, and nothing else.

Standard library only: `check_content_blind.py` is delivered into every governed repo as
an always-run pre-commit hook.

A match is evidence only when a checksum (Luhn, Verhoeff, ISO 7064, mod 11) or a schema
(positional meaning over closed enumerations) confirms it; the shape alone is not.
Separators are stripped before anything is decided.

Usage as a library:
    from identifiers import identify, identify_all
    identify("378282246310005")        -> "card-amex"
    identify("MH12 20110012345")       -> "in-dl"
    identify("hello")                  -> None

Usage from a shell (see scripts/pii.py for the CLI wrapper).

Complexity: O(patterns x text length), plus a bounded O(1) per-match checksum (Luhn,
Verhoeff, mod11) factor.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Iterator, NamedTuple

TABLE_PATH = Path(__file__).resolve().parent / "identifiers.json"

# ---------------------------------------------------------------------------
# The declaration
# ---------------------------------------------------------------------------


class TableError(ValueError):
    """`identifiers.json` declares something this module cannot bind, named by its entry.

    Raised when the table is loaded, never when a value is checked, so a table typo cannot
    read as "this candidate is not one" inside `Identifier.confirm`.
    """


def load_table(path: Path | None = None) -> dict[str, Any]:
    """Read and return the identifier declaration."""
    data: dict[str, Any] = json.loads((path or TABLE_PATH).read_text(encoding="utf-8"))
    return data


TABLE = load_table()
SCHEMES: dict[str, Any] = TABLE["schemes"]

# Boundary names resolve to lookaround. A port without lookaround composes its own
# anchoring from the `left`/`right` names.
_BOUNDARY_LEFT = {
    "word": r"\b",
    "not-digit": r"(?<![0-9])",
    "not-digit-or-dot": r"(?<![0-9.])",
    "none": "",
}
_BOUNDARY_RIGHT = {
    "word": r"\b",
    "not-digit": r"(?![0-9])",
    "not-digit-or-dot": r"(?![0-9.])",
    "none": "",
}


# ---------------------------------------------------------------------------
# Arithmetic: published algorithms; the data each operates on arrives from a scheme.
# ---------------------------------------------------------------------------

_ALNUM = {str(d): d for d in range(10)}
_ALNUM.update({chr(ord("A") + i): 10 + i for i in range(26)})


def luhn_ok(digits: str) -> bool:
    """The Luhn mod-10 check (ISO/IEC 7812-1), check digit included in the input."""
    total = 0
    for position, char in enumerate(reversed(digits)):
        value = int(char)
        if position % 2:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def iso7064_mod97_ok(value: str) -> bool:
    """ISO 7064 mod-97-10 over the alphanumeric-expanded value."""
    return int("".join(str(_ALNUM[c]) for c in value)) % 97 == 1


_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


def verhoeff_ok(digits: str) -> bool:
    """The Verhoeff dihedral-group check: Aadhaar and VID."""
    check = 0
    for position, char in enumerate(reversed(digits)):
        check = _VERHOEFF_D[check][_VERHOEFF_P[position % 8][int(char)]]
    return check == 0


def weighted_mod10_ok(digits: str, weights: list[int]) -> bool:
    """sum(digit * weight) mod 10 == 0. ABA routing uses 3-7-1."""
    return sum(int(d) * w for d, w in zip(digits, weights)) % 10 == 0


def mod11_remainder_ok(digits: str, weights: list[int], invalid: int) -> bool:
    """Weighted mod 11 with the check digit last; one remainder is never issued (NHS)."""
    remainder = sum(int(d) * w for d, w in zip(digits, weights)) % 11
    check = 11 - remainder
    if check == 11:
        check = 0
    if check == invalid:
        return False
    return check == int(digits[len(weights)])


def mod11_two_digit_ok(digits: str, variant: str, positions: list[int]) -> bool:
    """Two trailing mod-11 check digits (CPF, CNPJ), over the variant's weight scheme."""
    if len(set(digits)) == 1:
        return (
            False  # a repeated digit satisfies the arithmetic and is not an identifier
        )
    for length in positions:
        if variant == "descending":
            weights = list(range(length + 1, 1, -1))
        else:  # cycle-9-2, right-aligned
            weights = [((length - i - 1) % 8) + 2 for i in range(length)]
        remainder = sum(int(d) * w for d, w in zip(digits, weights)) % 11
        expected = 0 if remainder < 2 else 11 - remainder
        if int(digits[length]) != expected:
            return False
    return True


def cusip_ok(value: str) -> bool:
    """CUSIP: mod 10 over doubled alternate positions, letters valued A=10..Z=35."""
    total = 0
    for position, char in enumerate(value[:8]):
        v = _ALNUM[char]
        if position % 2:
            v *= 2
        total += v // 10 + v % 10
    return (10 - total % 10) % 10 == int(value[8])


def sedol_ok(value: str, weights: list[int]) -> bool:
    """SEDOL: weighted sum over the alphanumeric values, mod 10."""
    total = sum(_ALNUM[c] * w for c, w in zip(value, weights))
    return total % 10 == 0


def mod36_ok(value: str) -> bool:
    """Luhn mod 36 over the base-36 alphabet: the GSTIN check character."""
    factor, total, n = 2, 0, 36
    for char in reversed(value[:-1]):
        addend = factor * _ALNUM[char]
        factor = 1 if factor == 2 else 2
        total += addend // n + addend % n
    return (n - total % n) % n == _ALNUM[value[-1]]


_VIN_TRANSLIT = {
    **{str(d): d for d in range(10)},
    **dict(zip("ABCDEFGH", range(1, 9))),
    **dict(zip("JKLMNP", [1, 2, 3, 4, 5, 7])),
    **dict(zip("RSTUVWXYZ", [9, 2, 3, 4, 5, 6, 7, 8, 9])),
}


def vin_ok(value: str, weights: list[int], at: int) -> bool:
    """VIN: transliterate letters, weighted mod 11, X standing for a remainder of 10."""
    try:
        total = sum(_VIN_TRANSLIT[c] * w for c, w in zip(value, weights))
    except KeyError:
        return False
    remainder = total % 11
    expected = "X" if remainder == 10 else str(remainder)
    return value[at] == expected


# ---------------------------------------------------------------------------
# Schema checks. No arithmetic; a position has to mean something.
# ---------------------------------------------------------------------------


def _in_ranges(value: str, scheme: dict[str, Any]) -> bool:
    start, end = scheme["at"]
    n = int(value[start:end])
    return any(low <= n <= high for low, high in scheme["ranges"])


def _ssn_issuance_ok(digits: str, scheme: dict[str, Any]) -> bool:
    if digits in scheme.get("void", ()):
        return False  # issued, then withdrawn; identifies nobody, so not a disclosure
    area, group, serial = digits[:3], digits[3:5], digits[5:]
    return (
        area not in scheme["area_excluded"]
        and area[0] not in scheme["area_prefix_excluded"]
        and group not in scheme["group_excluded"]
        and serial not in scheme["serial_excluded"]
    )


def _nino_ok(value: str, scheme: dict[str, Any]) -> bool:
    return (
        value[0] not in scheme["letter1_excluded"]
        and value[1] not in scheme["letter2_excluded"]
        and value[:2] not in scheme["prefix_excluded"]
        and value[-1] in scheme["suffix_allowed"]
    )


def _cin_ok(value: str, scheme: dict[str, Any]) -> bool:
    """CIN: listing status and ownership type, at offsets the scheme names."""
    l_start, l_end = scheme["listing_at"]
    o_start, o_end = scheme["ownership_at"]
    return (
        value[l_start:l_end] in scheme["listing"]
        and value[o_start:o_end] in scheme["ownership"]
    )


def _at(value: str, scheme: dict[str, Any]) -> str:
    """The substring a scheme points at, via its two-element `at`."""
    start, end = scheme["at"]
    return value[start:end]


# One entry per scheme kind: the fields the check reads from the scheme, and the check.
# `_check_scheme` holds a scheme to these fields at load; add the field when adding a read.
_SchemeCheck = Callable[[str, dict[str, Any]], bool]
_SCHEME_KINDS: dict[str, tuple[tuple[str, ...], _SchemeCheck]] = {
    "ssn-issuance": (
        (
            "area_excluded",
            "area_prefix_excluded",
            "group_excluded",
            "serial_excluded",
        ),
        _ssn_issuance_ok,
    ),
    "digit-ranges": (("at", "ranges"), _in_ranges),
    "prefix-set": (("at", "values"), lambda v, s: _at(v, s) in s["values"]),
    "char-set": (("at", "values"), lambda v, s: v[s["at"]] in s["values"]),
    "char-set-2": (("at", "values"), lambda v, s: _at(v, s) in s["values"]),
    "nino-prefix": (
        ("letter1_excluded", "letter2_excluded", "prefix_excluded", "suffix_allowed"),
        _nino_ok,
    ),
    "cin-fields": (("listing_at", "ownership_at", "listing", "ownership"), _cin_ok),
    "code-map": (("at", "map"), lambda v, s: _at(v, s) in s["map"]),
    "luhn-substring": (("at",), lambda v, s: luhn_ok(_at(v, s))),
    "mod11-remainder": (
        ("weights", "invalid_remainder"),
        lambda v, s: mod11_remainder_ok(v, s["weights"], s["invalid_remainder"]),
    ),
    "weighted-mod10-variant": (
        ("variant",),
        lambda v, s: (
            cusip_ok(v) if s["variant"] == "cusip" else sedol_ok(v, s["weights"])
        ),
    ),
    "mod11-two-digit": (
        ("variant", "at"),
        lambda v, s: mod11_two_digit_ok(v, s["variant"], s["at"]),
    ),
}

# A `variant` is read by comparison, so a misspelt one would silently take the other
# branch. These are the spellings each kind's check tells apart, and the fields a variant
# adds to its kind's own.
_SCHEME_VARIANTS: dict[str, dict[str, tuple[str, ...]]] = {
    "weighted-mod10-variant": {"cusip": (), "sedol": ("weights",)},
    "mod11-two-digit": {"descending": (), "cycle-9-2": ()},
}


def _scheme_check(value: str, name: str | None) -> bool:
    """Dispatch a named scheme to the check its `kind` calls for. A missing `name` raises."""
    if name is None:
        raise KeyError("identifiers: a schema check requires check.scheme")
    scheme = SCHEMES[name]
    try:
        _fields, kind = _SCHEME_KINDS[scheme["kind"]]
    except KeyError as exc:
        raise KeyError(
            f"identifiers: unknown scheme kind {scheme['kind']!r} for {name!r}"
        ) from exc
    return kind(value, scheme)


def _check_scheme(name: str, schemes: dict[str, Any]) -> None:
    """Refuse a scheme reference `_scheme_check` could not follow when a value arrives."""
    if name not in schemes:
        raise TableError(f"names scheme {name!r}, which `schemes` does not declare")
    scheme = schemes[name]
    kind = scheme.get("kind")
    if kind not in _SCHEME_KINDS:
        raise TableError(f"scheme {name!r} has unknown kind {kind!r}")
    required = list(_SCHEME_KINDS[kind][0])
    if kind in _SCHEME_VARIANTS:
        variants = _SCHEME_VARIANTS[kind]
        variant = scheme.get("variant")
        if variant not in variants:
            raise TableError(
                f"scheme {name!r} has variant {variant!r}; {kind} reads "
                f"{', '.join(sorted(variants))}"
            )
        required += variants[variant]
    missing = [f for f in required if f not in scheme]
    if missing:
        raise TableError(
            f"scheme {name!r} ({kind}) lacks {', '.join(missing)}, which its check reads"
        )


def _check_fields(check: dict[str, Any]) -> tuple[str, ...]:
    """What a row's `check` must carry beyond `algorithm`, read only when a value arrives.

    A function of the row: `weighted-mod10` and `weighted-mod11` each have two shapes.
    """
    algorithm = check.get("algorithm", "none")
    scheme = check.get("scheme")
    if algorithm in {"schema", "mod11-two-digit"}:
        return ("scheme",)
    if algorithm == "luhn-prefixed":
        return ("prefix",)
    if algorithm == "weighted-mod10":
        return () if scheme in {"cusip", "sedol"} else ("weights",)
    if algorithm == "weighted-mod11":
        return ("weights", "at") if check.get("transliterate") == "vin" else ("scheme",)
    return ()


def _check_row(row: dict[str, Any], schemes: dict[str, Any]) -> None:
    """Resolve, at load, every reference a row's validator would otherwise follow late."""
    check = row.get("check", {})
    missing = [f for f in _check_fields(check) if not check.get(f)]
    if missing:
        raise TableError(
            f"check {check.get('algorithm')!r} requires {', '.join(missing)}"
        )
    if check.get("scheme"):
        _check_scheme(check["scheme"], schemes)
    yields = row.get("yields") or {}
    if "subdivision" in yields:
        _check_scheme(yields["subdivision"]["map"], schemes)


# ---------------------------------------------------------------------------
# Binding a row to its validator
# ---------------------------------------------------------------------------


def _validator_for(row: dict[str, Any]) -> Callable[[str], bool]:
    """Return the callable that confirms a normalised match for this row."""
    check = row.get("check", {})
    algorithm = check.get("algorithm", "none")
    scheme = check.get("scheme")

    builders: dict[str, Callable[[], Callable[[str], bool]]] = {
        "none": lambda: (lambda _v: True),
        # A `luhn` row with a scheme runs the scheme (EPIC checks only its serial).
        "luhn": lambda: (lambda v: _scheme_check(v, scheme)) if scheme else luhn_ok,
        "luhn-prefixed": lambda: (lambda v: luhn_ok(check["prefix"] + v)),
        "luhn-alnum": lambda: (lambda v: luhn_ok("".join(str(_ALNUM[c]) for c in v))),
        "iso7064-mod97": lambda: iso7064_mod97_ok,
        "iso7064-mod97-iban": lambda: (lambda v: iso7064_mod97_ok(v[4:] + v[:4])),
        "verhoeff": lambda: verhoeff_ok,
        "mod36": lambda: mod36_ok,
        # The named scheme decides outright; there is no negating variant.
        "schema": lambda: (lambda v: _scheme_check(v, scheme)),
        "mod11-two-digit": lambda: (lambda v: _scheme_check(v, scheme)),
        "weighted-mod10": lambda: _weighted_mod10_validator(check, scheme),
        "weighted-mod11": lambda: _weighted_mod11_validator(check, scheme),
    }
    try:
        return builders[algorithm]()
    except KeyError as exc:
        raise KeyError(
            f"identifiers: unknown algorithm {algorithm!r} in row {row['name']!r}"
        ) from exc


def _weighted_mod10_validator(
    check: dict[str, Any], scheme: str | None
) -> Callable[[str], bool]:
    """CUSIP and SEDOL carry their own variants; ABA pairs weights with a prefix rule."""
    if scheme in {"cusip", "sedol"}:
        return lambda v: _scheme_check(v, scheme)
    weights = check["weights"]
    if scheme:
        return lambda v: weighted_mod10_ok(v, weights) and _scheme_check(v, scheme)
    return lambda v: weighted_mod10_ok(v, weights)


def _weighted_mod11_validator(
    check: dict[str, Any], scheme: str | None
) -> Callable[[str], bool]:
    """VIN transliterates before weighting; NHS is a plain weighted remainder."""
    if check.get("transliterate") == "vin":
        weights, at = check["weights"], check["at"]
        return lambda v: vin_ok(v, weights, at)
    return lambda v: _scheme_check(v, scheme)


def _pattern_for(row: dict[str, Any], scan_rule: dict[str, Any]) -> re.Pattern[str]:
    """Compose the search pattern from the row's shape (or its card family fields)."""
    if row.get("kind") == "payment-card":
        # Separators are stripped before anything is judged, so the pattern only has to
        # find a candidate: digit runs joined by single spaces or hyphens, in any
        # arrangement.
        longest = max(row["lengths"])
        body = scan_rule["candidate"]
        return (
            re.compile(
                _BOUNDARY_LEFT["not-digit-or-dot"] + body + _BOUNDARY_RIGHT["not-digit"]
            )
            if longest
            else re.compile(body)
        )
    left = _BOUNDARY_LEFT[row.get("left", "word")]
    right = _BOUNDARY_RIGHT[row.get("right", "word")]
    return re.compile(left + "(?:" + row["shape"] + ")" + right)


class Identifier(NamedTuple):
    """One bound row: how to find a candidate, and how to confirm it."""

    name: str
    label: str
    country: str | None
    subdivision: str | None
    kind: str
    klass: str
    anchored: bool
    note: str
    pattern: re.Pattern[str]
    normalize: str
    validator: Callable[[str], bool]
    row: dict[str, Any]

    def confirm(self, raw: str) -> bool:
        """True when `raw`, once normalised, satisfies this type's rule."""
        value = normalise(raw, self.normalize)
        if self.kind == "payment-card":
            if len(value) not in self.row["lengths"] or not value.isdigit():
                return False
            if not re.match("(?:" + self.row["iin"] + ")", value):
                return False
            return luhn_ok(value)
        # What this catches is about the candidate (a character with no value, a slice
        # past its end); table references are resolved at load (`_build`).
        try:
            return self.validator(value)
        except (KeyError, ValueError, IndexError):
            return False


def normalise(raw: str, separators: str) -> str:
    """Strip the row's declared separators. Presentation goes; the value stays."""
    for ch in separators:
        raw = raw.replace(ch, "")
    return raw


def _build(table: dict[str, Any]) -> tuple[Identifier, ...]:
    """Bind every declared row to a compiled pattern and a validator.

    Raises `TableError` naming the row when any of it cannot be bound.
    """
    scan_rule = table["payment_card_scan"]
    built: list[Identifier] = []
    for row in table["identifiers"]:
        try:
            built.append(_bind(row, scan_rule, table["schemes"]))
        except (KeyError, TypeError, ValueError, re.error) as exc:
            name = row.get("name", "<a row with no name>")
            raise TableError(f"identifiers.json: row {name!r}: {exc}") from exc
    return tuple(built)


def _bind(
    row: dict[str, Any], scan_rule: dict[str, Any], schemes: dict[str, Any]
) -> Identifier:
    """One row, bound; raises on anything in it that cannot be."""
    _check_row(row, schemes)
    return Identifier(
        name=row["name"],
        label=row.get("label", row["name"]),
        country=row.get("country"),
        subdivision=row.get("subdivision"),
        kind=row.get("kind", "generic"),
        klass=row.get("class", "unknown"),
        anchored=bool(row.get("anchored", True)),
        note=row.get("note", ""),
        pattern=_pattern_for(row, scan_rule),
        normalize=row.get("normalize", "")
        or ("" if row.get("kind") != "payment-card" else scan_rule["separators"]),
        validator=_validator_for(row),
        row=row,
    )


IDENTIFIERS: tuple[Identifier, ...] = _build(TABLE)
BY_NAME: dict[str, Identifier] = {i.name: i for i in IDENTIFIERS}


# ---------------------------------------------------------------------------
# The public surface
# ---------------------------------------------------------------------------


# How much a row proved, for ranking a value that two rules both accept: arithmetic, then
# schema, then bare shape.
_EVIDENCE_RANK = {
    "none": 0,  # shape alone
    "schema": 1,  # positional meaning over closed enumerations
}


def _specificity(spec: Identifier) -> tuple[int, int]:
    """Sort key: stronger evidence first, then the longer (more constrained) shape."""
    algorithm = spec.row.get("check", {}).get("algorithm", "none")
    rank = _EVIDENCE_RANK.get(algorithm, 2)  # anything arithmetic
    width = (
        max(spec.row["lengths"])
        if spec.kind == "payment-card"
        else len(spec.row.get("shape", ""))
    )
    return (-rank, -width)


def identify_all(value: str, anchored_only: bool = False) -> list[str]:
    """Every type whose rule the whole string satisfies, strongest evidence first.

    More than one answer is a real outcome: a ten-digit number can satisfy both the NHS
    modulus-11 check and the NPI Luhn-over-80840 check. Ranked by what each row proved:
    arithmetic, then a schema over closed enumerations, then shape alone; ties broken by
    the more constrained shape.
    """
    found: list[Identifier] = []
    for spec in IDENTIFIERS:
        if anchored_only and not spec.anchored:
            continue
        match = spec.pattern.fullmatch(value.strip())
        if match and spec.confirm(match.group(0)):
            found.append(spec)
    return [spec.name for spec in sorted(found, key=_specificity)]


def identify(value: str, anchored_only: bool = False) -> str | None:
    """The best-ranked answer for a complete string, or None when nothing claims it.

    Lossy where two rules both accept the value; use `identify_all` or `describe`.
    """
    names = identify_all(value, anchored_only)
    return names[0] if names else None


def describe(value: str, anchored_only: bool = False) -> dict[str, Any] | None:
    """Identify, and say everything the declaration knows about the result.

    Where the value encodes a subdivision (an Indian licence naming its issuing state),
    it is resolved through the map the scheme carries. `anchored_only` behaves as in
    `identify`/`identify_all`/`scan`.
    """
    names = identify_all(value, anchored_only=anchored_only)
    if not names:
        return None
    name = names[0]
    spec = BY_NAME[name]
    out: dict[str, Any] = {
        "type": name,
        "label": spec.label,
        "country": spec.country,
        "subdivision": spec.subdivision,
        "class": spec.klass,
        "note": spec.note,
        # Reported: where two rules accept a value, only the caller can settle it.
        "also_claimed_by": names[1:],
        "source": spec.row.get("source"),
    }
    yields = spec.row.get("yields")
    if yields and "subdivision" in yields:
        scheme = SCHEMES[yields["subdivision"]["map"]]
        start, end = scheme["at"]
        out["subdivision"] = scheme["map"].get(
            normalise(value.strip(), spec.normalize)[start:end]
        )
    return out


def scan(
    text: str,
    anchored_only: bool = False,
    disabled: frozenset[str] = frozenset(),
) -> Iterator[tuple[str, str]]:
    """Yield (type, matched text) for every identifier occurring anywhere in `text`.

    `disabled` types are excluded from the pattern set before `finditer` runs.
    """
    for spec in IDENTIFIERS:
        if anchored_only and not spec.anchored:
            continue
        if spec.name in disabled:
            continue
        for match in spec.pattern.finditer(text):
            raw = match.group(0)
            if spec.confirm(raw):
                yield spec.name, raw

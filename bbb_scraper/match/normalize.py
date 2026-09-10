"""
Normalization + similarity primitives for record linkage.

Pure functions, no I/O, stdlib only (difflib for string similarity -- the
project is dependency-lean on purpose; swap in rapidfuzz here later if name
matching quality ever demands it, nothing else needs to change).
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

# Dropped from business names before matching: legal form + filler that
# carries little identifying signal. Industry-specific noise ("auto",
# "motors", ...) is intentionally NOT here -- dropping it over-collapses
# distinct names; callers can pass extra drop tokens when they know the
# vertical.
_NAME_NOISE = {
    "inc", "incorporated", "llc", "llp", "lllp", "lp", "corp", "corporation",
    "co", "company", "ltd", "limited", "pllc", "pc", "pa", "group", "holdings",
    "enterprises", "the", "of", "and", "at", "a",
}

# Words so common across unrelated business names that sharing one is not
# evidence two records are the same business. Used ONLY by name_similarity's
# distinctiveness gate -- these are NOT stripped from the names themselves
# (that would over-collapse "Machado Auto Sales" -> "Machado").
_GENERIC_NAME_WORDS = {
    "auto", "autos", "motor", "motors", "automotive", "car", "cars", "truck",
    "trucks", "sales", "sale", "service", "services", "repair", "center",
    "centre", "shop", "store", "outlet", "dealer", "dealers", "used", "new",
    "preowned", "wholesale", "broker", "brokers", "import", "imports", "export",
    "exports", "trading", "rental", "rentals", "express", "group", "usa",
    "america", "american", "national", "international", "enterprise",
    "enterprises", "industries", "solutions", "associates", "partners",
}

_LETTER_GRADE_TO_NUM = {
    "A+": 4.33, "A": 4.0, "A-": 3.67,
    "B+": 3.33, "B": 3.0, "B-": 2.67,
    "C+": 2.33, "C": 2.0, "C-": 1.67,
    "D+": 1.33, "D": 1.0, "D-": 0.67,
    "F": 0.0,
}


def phone_key(raw: str | None) -> str | None:
    """Bare 10-digit US phone, or None. Drops a leading country '1'.
    ('(305) 642-4409' and '+13056424409' both -> '3056424409')."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def zip5(raw: str | None) -> str | None:
    if not raw:
        return None
    m = re.search(r"\d{5}", str(raw))
    return m.group(0) if m else None


def city_key(raw: str | None) -> str:
    if not raw:
        return ""
    return re.sub(r"[^a-z0-9 ]", "", str(raw).lower()).strip()


def _name_token_list(raw: str | None, *, extra_drop: set[str] | None = None) -> list[str]:
    """Lowercased tokens, punctuation stripped, '&'->'and', legal-form /
    filler tokens removed. Order preserved (dupes kept)."""
    if not raw:
        return []
    text = str(raw).lower().replace("&", " and ")
    text = text.replace("'", "").replace("’", "")  # "Fuego's" -> "fuegos", not "fuego s"
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    drop = _NAME_NOISE | (extra_drop or set())
    # Single letters are kept -- "J & B Auto", "G S Auto Sales": the
    # initials ARE the distinctive part. Only the bare article "a" is
    # dropped (it's in _NAME_NOISE).
    return [t for t in text.split() if t and t not in drop]


def name_tokens(raw: str | None, *, extra_drop: set[str] | None = None) -> frozenset[str]:
    """The token set of a name (see _name_token_list)."""
    return frozenset(_name_token_list(raw, extra_drop=extra_drop))


def name_similarity(
    a: str | None, b: str | None, *, extra_drop: set[str] | None = None
) -> float:
    """0..1 name similarity. Robust to word order, legal suffixes, and one
    name being a superset of the other ('Coggin Honda' vs 'Coggin Honda
    Jacksonville'). Deliberately does NOT let a shared generic tail ('...
    Auto Sales') alone score two different businesses as similar -- the
    character-level score is computed on the names in their original word
    order, so 'Green Light Auto Sales' vs 'Bird Road Auto Sales' stays low.
    """
    la = _name_token_list(a, extra_drop=extra_drop)
    lb = _name_token_list(b, extra_drop=extra_drop)
    if not la or not lb:
        return 0.0
    sa, sb = set(la), set(lb)
    inter = sa & sb
    jaccard = len(inter) / len(sa | sb)
    containment = len(inter) / min(len(sa), len(sb))
    seq = SequenceMatcher(None, " ".join(la), " ".join(lb)).ratio()
    score = max(seq, 0.5 * containment + 0.5 * jaccard)

    # Distinctiveness gate. da/db are each name's non-generic words.
    #  - one side is ALL generic ('G S Auto Sales' -> nothing distinctive):
    #    it can't anchor a match on its own -> hard cap.
    #  - both distinctive but zero overlap ('Green Light' vs 'Bird Road'):
    #    different businesses despite a shared '... Auto Sales' tail.
    da, db = sa - _GENERIC_NAME_WORDS, sb - _GENERIC_NAME_WORDS
    if not da or not db:
        score = min(score, 0.50)
    elif not (da & db):
        score = min(score, 0.45)
    return round(score, 4)


def letter_grade_to_num(grade: str | None) -> float | None:
    """BBB letter grade -> 0..4.33 GPA-style number. 'NR'/None -> None."""
    if not grade:
        return None
    return _LETTER_GRADE_TO_NUM.get(str(grade).strip().upper().replace(" ", ""))

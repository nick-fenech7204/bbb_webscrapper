"""
Score and assign BBB <-> Yelp record pairs.

Signals (street address deliberately excluded):
  phone   0.40   exact match on 10-digit key -> 1.0, else 0.0
  name    0.34   token-set similarity (normalize.name_similarity)
  geo     0.16   1 - dist_mi/2, clamped 0..1 (both coords required)
  zip     0.06   exact ZIP5 -> 1.0 ; same city+state -> 0.4 ; else 0
  city    0.04   same city_key + state -> 1.0, else 0

Confidence is a weighted mean over the signals that could actually be
evaluated (a missing geo signal doesn't dilute a strong phone+name pair --
its weight drops out of the denominator). A confirmed phone match with a
plausible name (>0.55) is floored at 0.90: on this data a shared landline
is decisive.

Assignment is greedy 1:1: candidates sorted by score desc, a pair taken
when neither side is spoken for. Explainable and, at these sizes, as good
as optimal. Every pair keeps its per-signal breakdown for auditing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from bbb_scraper.logging_setup import get_logger
from bbb_scraper.match.normalize import (
    city_key,
    name_similarity,
    phone_key,
    zip5,
)
from bbb_scraper.reference.geo import distance_miles

logger = get_logger(__name__)

_WEIGHTS = {"phone": 0.40, "name": 0.34, "geo": 0.16, "zip": 0.06, "city": 0.04}

DEFAULT_CONFIDENT = 0.80
DEFAULT_REVIEW = 0.60
GEO_FULL_CREDIT_MILES = 2.0
BLOCK_DISTANCE_MILES = 3.0


@dataclass
class _Rec:
    idx: int
    raw: dict[str, Any]
    phone: str | None
    name: str
    zip: str | None
    city: str
    state: str
    lat: float | None
    lon: float | None


@dataclass
class MatchedPair:
    bbb: dict[str, Any]
    yelp: dict[str, Any]
    confidence: float
    band: str  # "confident" | "review"
    signals: dict[str, float]


@dataclass
class MatchOutcome:
    pairs: list[MatchedPair] = field(default_factory=list)
    bbb_only: list[dict[str, Any]] = field(default_factory=list)
    yelp_only: list[dict[str, Any]] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, int]:
        return {
            "bbb_total": len(self.bbb_only) + len(self.pairs),
            "yelp_total": len(self.yelp_only) + len(self.pairs),
            "matched": len(self.pairs),
            "confident": sum(p.band == "confident" for p in self.pairs),
            "review": sum(p.band == "review" for p in self.pairs),
            "bbb_only": len(self.bbb_only),
            "yelp_only": len(self.yelp_only),
        }


def _as_float(v: Any) -> float | None:
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _prep(records: list[dict[str, Any]], *, name_field: str = "name") -> list[_Rec]:
    out = []
    for i, r in enumerate(records):
        out.append(
            _Rec(
                idx=i,
                raw=r,
                phone=phone_key(r.get("phone") or r.get("display_phone")),
                name=str(r.get(name_field) or ""),
                zip=zip5(r.get("postal_code") or r.get("zip_code")),
                city=city_key(r.get("city")),
                state=str(r.get("state") or "").strip().upper(),
                lat=_as_float(r.get("lat")),
                lon=_as_float(r.get("lon")),
            )
        )
    return out


def _score(b: _Rec, y: _Rec, *, name_extra_drop: set[str] | None) -> dict[str, float]:
    sig: dict[str, float] = {}

    if b.phone and y.phone:
        sig["phone"] = 1.0 if b.phone == y.phone else 0.0

    sig["name"] = name_similarity(b.name, y.name, extra_drop=name_extra_drop)

    if None not in (b.lat, b.lon, y.lat, y.lon):
        d = distance_miles(b.lat, b.lon, y.lat, y.lon)
        sig["geo"] = max(0.0, 1.0 - d / GEO_FULL_CREDIT_MILES)

    if b.zip and y.zip:
        if b.zip == y.zip:
            sig["zip"] = 1.0
        elif b.city and b.city == y.city and b.state == y.state:
            sig["zip"] = 0.4
        else:
            sig["zip"] = 0.0

    if b.city and y.city:
        sig["city"] = 1.0 if (b.city == y.city and b.state == y.state) else 0.0

    return sig


def _confidence(sig: dict[str, float]) -> float:
    avail = {k: w for k, w in _WEIGHTS.items() if k in sig}
    if not avail:
        return 0.0
    total = sum(w for w in avail.values())
    conf = sum(sig[k] * w for k, w in avail.items()) / total
    if sig.get("phone") == 1.0 and sig.get("name", 0.0) >= 0.55:
        conf = max(conf, 0.90)
    return round(conf, 4)


def match_datasets(
    bbb_records: list[dict[str, Any]],
    yelp_records: list[dict[str, Any]],
    *,
    confident_threshold: float = DEFAULT_CONFIDENT,
    review_threshold: float = DEFAULT_REVIEW,
    name_extra_drop: set[str] | None = None,
) -> MatchOutcome:
    bbb = _prep(bbb_records)
    yelp = _prep(yelp_records)

    # --- block: only score pairs sharing a phone, a ZIP, or within 3mi ---
    by_phone: dict[str, list[int]] = {}
    by_zip: dict[str, list[int]] = {}
    for y in yelp:
        if y.phone:
            by_phone.setdefault(y.phone, []).append(y.idx)
        if y.zip:
            by_zip.setdefault(y.zip, []).append(y.idx)

    candidates: list[tuple[float, int, int, dict[str, float]]] = []
    seen_pairs: set[tuple[int, int]] = set()
    for b in bbb:
        cand_y: set[int] = set()
        if b.phone:
            cand_y.update(by_phone.get(b.phone, ()))
        if b.zip:
            cand_y.update(by_zip.get(b.zip, ()))
        if None not in (b.lat, b.lon):
            for y in yelp:
                if None not in (y.lat, y.lon) and distance_miles(b.lat, b.lon, y.lat, y.lon) <= BLOCK_DISTANCE_MILES:
                    cand_y.add(y.idx)
        for yi in cand_y:
            if (b.idx, yi) in seen_pairs:
                continue
            seen_pairs.add((b.idx, yi))
            sig = _score(b, yelp[yi], name_extra_drop=name_extra_drop)
            # No phone confirmation AND weak name -> a ZIP/geo coincidence,
            # not a match. Don't let it through on location alone.
            if sig.get("phone") != 1.0 and sig.get("name", 0.0) < 0.50:
                continue
            conf = _confidence(sig)
            if conf >= review_threshold:
                candidates.append((conf, b.idx, yi, sig))

    # --- greedy 1:1 assignment, best score first ---
    candidates.sort(key=lambda t: t[0], reverse=True)
    used_b: set[int] = set()
    used_y: set[int] = set()
    outcome = MatchOutcome()
    for conf, bi, yi, sig in candidates:
        if bi in used_b or yi in used_y:
            continue
        used_b.add(bi)
        used_y.add(yi)
        outcome.pairs.append(
            MatchedPair(
                bbb=bbb[bi].raw,
                yelp=yelp[yi].raw,
                confidence=conf,
                band="confident" if conf >= confident_threshold else "review",
                signals=sig,
            )
        )

    outcome.bbb_only = [b.raw for b in bbb if b.idx not in used_b]
    outcome.yelp_only = [y.raw for y in yelp if y.idx not in used_y]
    logger.info("match_datasets -> %s", outcome.summary)
    return outcome

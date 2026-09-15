"""Pick the right MapQuestMatch out of a search's candidate nodes -- a
search for a business name can return several same/near-named businesses
(confirmed real: two completely different real businesses this project
already tracks each came back with 2-3 candidate nodes, including one
same-named decoy in a different state entirely). Phone-first, the same
decisive-signal choice this project already made for Angi
(bbb_scraper/angi/enrich.py) -- simpler and more honest than a fuzzy
multi-signal score, and confirmed live on two real businesses (Platinum
Exteriors, RENCO Roofing): an exact phone match's reviews.totalCount was
identical to what this project's own Yelp match already recorded for the
same business, independently cross-confirming both sources agree.

**Real wrinkle, caught live, not assumed away:** the phone-matched
candidate isn't always the one carrying the reviews. Checked against the
exact business from Nick's own original find (Walsh Crawlspace) using its
real BBB-listed phone: the phone match correctly resolved to the
BBB-verified entity -- which had review_count=0. A second, same-named
candidate a few blocks away (MapQuest's own listing for what's very likely
the same real business, just a service-area listing under a different
contact number) carried all 5 real reviews. Since the entire point of this
module is harvesting review content, silently returning the "correctly
identified but empty" match would be a real, meaningful data loss for the
actual use case -- so a phone match with zero reviews is no longer
treated as final; see find_business's own logic below for exactly when a
review-carrying sibling can override it, and how conservative that
override is kept.
"""
from __future__ import annotations

from bbb_scraper.mapquest.models import MapQuestMatch
from bbb_scraper.match.normalize import name_similarity, phone_key

# Only used when there's no phone to check against at all -- a much weaker
# signal than an exact phone match, so it needs a much higher bar before
# trusting it. Not reached for the common case (most BBB records do carry
# a phone) -- see _contact_readiness in bbb_scraper/match/merge.py.
_NAME_ONLY_MATCH_THRESHOLD = 0.85

# Higher bar than the name-only path above, deliberately -- this one is
# overriding a phone match that resolved to a real, identity-confirmed
# business with zero reviews, not filling in for a missing phone signal
# entirely. Only worth taking the identity-certainty hit for a near-exact
# name (see the module docstring's real Walsh Crawlspace case: "Walsh Crawl
# Space and Structural Repair" vs. "Walsh Crawlspace & Structural Repair
# LLC" scores well above this).
_SIBLING_WITH_REVIEWS_THRESHOLD = 0.90


def find_business(
    candidates: list[MapQuestMatch], *, name: str, phone: str | None = None,
) -> MapQuestMatch | None:
    """None if nothing in `candidates` looks like a confident match --
    never guesses.

    With a real `phone` to check: an exact phone_key match is the primary
    signal. If that match has real reviews, it's returned immediately. If
    it has zero reviews, a same-named (>= 0.90 name-similarity) sibling
    candidate that DOES carry reviews is preferred instead (see the module
    docstring for the real case this handles) -- otherwise the phone
    match is returned anyway, reviews or not, rather than falling through
    to a weaker signal. A real phone with no match at all in `candidates`
    returns None outright (not a guess by name) -- this only relaxes
    identity certainty to rescue review data from an already-confirmed
    match, never to invent one from nothing.

    With no `phone` at all: falls back to the best name-similarity match,
    requiring a higher bar (0.85) since there's no phone corroborating it.
    """
    if not candidates:
        return None

    target_phone = phone_key(phone) if phone else None
    if target_phone:
        phone_match = next((c for c in candidates if phone_key(c.phone) == target_phone), None)
        if phone_match is None:
            return None
        if phone_match.reviews or phone_match.review_count:
            return phone_match

        # Search only among candidates that actually carry reviews, THEN
        # pick the best name match among those -- not "find the overall
        # best name match, then check if it happens to have reviews".
        # With 3+ candidates those can disagree: the single closest name
        # match could be a different zero-review listing while a slightly
        # less-close (but still >= the threshold) sibling is the one
        # actually carrying the reviews this whole override exists to find.
        review_carrying = [c for c in candidates if c is not phone_match and (c.reviews or c.review_count)]
        sibling = _best_name_match(review_carrying, name)
        if sibling is not None and name_similarity(name, sibling.name or "") >= _SIBLING_WITH_REVIEWS_THRESHOLD:
            return sibling
        return phone_match

    sibling = _best_name_match(candidates, name)
    if sibling is None:
        return None
    return sibling if name_similarity(name, sibling.name or "") >= _NAME_ONLY_MATCH_THRESHOLD else None


def _best_name_match(candidates: list[MapQuestMatch], name: str) -> MapQuestMatch | None:
    best: MapQuestMatch | None = None
    best_score = 0.0
    for c in candidates:
        score = name_similarity(name, c.name or "")
        if score > best_score:
            best, best_score = c, score
    return best

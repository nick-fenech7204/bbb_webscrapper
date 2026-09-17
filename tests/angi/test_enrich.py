"""bbb_scraper.angi.enrich -- phone-based matching of Angi records onto an
already-built master table, PLUS (2026-09-17) creating genuinely new
angi_only rows for Angi businesses with no BBB match at all. See
tests/match/test_merge.py for the scoring side (lead_priority_score etc.
responding to angi_* fields)."""
from bbb_scraper.angi.enrich import enrich_with_angi


def _master_row(**overrides) -> dict:
    row = {"match_status": "bbb_only", "bbb_name": "Co", "bbb_phone": "(305) 555-0100",
           "bbb_rating": "B", "bbb_city": "Miami", "bbb_postal_code": "33101"}
    row.update(overrides)
    return row


def test_matches_by_exact_phone_and_fills_angi_fields():
    rows = [_master_row()]
    angi = [{"name": "Co on Angi", "phone": "3055550100", "overall_rating": "4.5",
             "review_count": "20", "categories": "Plumbing; Drain Cleaning"}]
    out = enrich_with_angi(rows, angi)
    assert len(out) == 1  # phone-matched onto the existing row, no new row
    assert out[0]["angi_name"] == "Co on Angi"
    assert out[0]["angi_overall_rating"] == "4.5"
    assert out[0]["angi_categories"] == "Plumbing; Drain Cleaning"
    assert out[0]["on_angi"] == 1


def test_phone_formatting_differences_still_match():
    """(305) 555-0100, 3055550100, and +1-305-555-0100 are the same phone."""
    rows = [_master_row(bbb_phone="+1 (305) 555-0100")]
    angi = [{"name": "Co", "phone": "305.555.0100"}]
    out = enrich_with_angi(rows, angi)
    assert len(out) == 1
    assert out[0]["on_angi"] == 1


def test_no_matching_phone_or_place_leaves_row_unmatched_and_creates_a_new_one():
    """2026-09-17: an Angi business with no phone match AND no plausible
    name+city/zip overlap with any existing row is a genuinely different
    business -- it no longer just vanishes, it becomes its own angi_only
    row."""
    rows = [_master_row(bbb_phone="3055550100", bbb_city="Miami", bbb_postal_code="33101")]
    angi = [{"name": "Someone Else", "phone": "9545551234", "city": "Orlando", "zip_code": "32801",
             "overall_rating": "4.0", "review_count": "30"}]
    out = enrich_with_angi(rows, angi)
    assert len(out) == 2
    assert out[0]["angi_name"] == ""  # original row still unmatched
    assert out[0]["on_angi"] == 0
    new_row = out[1]
    assert new_row["match_status"] == "angi_only"
    assert new_row["angi_name"] == "Someone Else"
    assert new_row["bbb_name"] == ""  # never faked from Angi data
    assert new_row["bbb_phone"] == ""
    assert new_row["on_angi"] == 1
    assert new_row["lead_priority_score"] is not None  # scored off its real Angi rating like any other row


def test_no_phone_match_but_strong_name_and_city_match_attaches_not_duplicates():
    """A business that changed phone numbers on one platform but not the
    other -- the exact real-world case the fallback exists for. Same real
    business, so this must enrich the existing row, not create a second one
    for what's really the same business."""
    rows = [_master_row(bbb_name="Wyattworks Plumbing Inc", bbb_phone="3055550100",
                          bbb_city="Charlotte", bbb_postal_code="28202")]
    angi = [{"name": "Wyattworks Plumbing, Inc.", "phone": "7045559999",  # different phone
             "city": "Charlotte", "zip_code": "28202", "overall_rating": "4.5"}]
    out = enrich_with_angi(rows, angi)
    assert len(out) == 1  # attached, not a new row
    assert out[0]["angi_name"] == "Wyattworks Plumbing, Inc."
    assert out[0]["angi_overall_rating"] == "4.5"


def test_same_city_but_different_business_stays_two_separate_rows():
    """City matches, but the names are genuinely unrelated -- must NOT
    merge. A false merge silently drops a real, distinct business, which
    is worse than a false split (see _NAME_MATCH_THRESHOLD's own comment)."""
    rows = [_master_row(bbb_name="Wyattworks Plumbing Inc", bbb_phone="3055550100",
                          bbb_city="Charlotte", bbb_postal_code="28202")]
    angi = [{"name": "Morris-Jenkins Heating and Air", "phone": "7045559999",
             "city": "Charlotte", "zip_code": "28202"}]
    out = enrich_with_angi(rows, angi)
    assert len(out) == 2
    assert out[0]["angi_name"] == ""  # not wrongly attached
    assert out[1]["match_status"] == "angi_only"
    assert out[1]["angi_name"] == "Morris-Jenkins Heating and Air"


def test_no_matching_phone_leaves_that_rows_angi_fields_blank():
    rows = [_master_row(bbb_phone="3055550100", bbb_city="Miami")]
    angi = [{"name": "Someone Else", "phone": "9545551234", "city": "Orlando"}]
    out = enrich_with_angi(rows, angi)
    assert out[0]["angi_name"] == ""
    assert out[0]["on_angi"] == 0


def test_row_with_no_phone_and_no_place_data_is_left_unmatched_not_a_crash():
    rows = [_master_row(bbb_phone="", bbb_city="")]
    angi = [{"name": "Co", "phone": "3055550100", "city": "Miami"}]
    out = enrich_with_angi(rows, angi)
    assert out[0]["angi_name"] == ""


def test_does_not_mutate_input_rows():
    rows = [_master_row()]
    angi = [{"name": "Co on Angi", "phone": "3055550100"}]
    enrich_with_angi(rows, angi)
    assert "angi_name" not in rows[0]


def test_recomputes_intel_so_lead_priority_score_reflects_the_new_match():
    rows = [_master_row(bbb_rating="A+")]
    angi = [{"name": "Co", "phone": "3055550100", "overall_rating": "2.5", "review_count": "30"}]
    out = enrich_with_angi(rows, angi)
    # A+ alone bands low (22, "already fine"); a fixable matched Angi
    # rating (in the salvageable-middle band) should pull it up, reflected
    # without the caller having to call recompute_intel separately.
    only_bbb_score = enrich_with_angi(rows, [])[0]["lead_priority_score"]
    assert out[0]["lead_priority_score"] > only_bbb_score


def test_duplicate_angi_phone_keeps_the_first_seen_and_does_not_crash():
    rows = [_master_row()]
    angi = [
        {"name": "First Co", "phone": "3055550100"},
        {"name": "Second Co", "phone": "3055550100"},
    ]
    out = enrich_with_angi(rows, angi)
    assert out[0]["angi_name"] == "First Co"


def test_duplicate_angi_phone_second_record_never_becomes_a_spurious_new_row():
    """2026-09-17: the second same-phone Angi record is the same real
    business as the first (see the module docstring) -- it must never
    become its own angi_only row just because it wasn't the one that
    happened to get attached to the matching BBB row."""
    rows = [_master_row(bbb_phone="3055550100")]
    angi = [
        {"name": "First Co", "phone": "3055550100", "city": "Somewhere Else"},
        {"name": "Second Co Branch", "phone": "3055550100", "city": "Yet Another City"},
    ]
    out = enrich_with_angi(rows, angi)
    assert len(out) == 1  # not 2 -- the duplicate-phone record never becomes a new row


def test_many_master_rows_sharing_one_phone_all_get_the_same_match():
    rows = [_master_row(), _master_row()]
    angi = [{"name": "Co on Angi", "phone": "3055550100"}]
    out = enrich_with_angi(rows, angi)
    assert out[0]["angi_name"] == out[1]["angi_name"] == "Co on Angi"


def test_empty_angi_records_leaves_every_row_unmatched():
    rows = [_master_row(), _master_row(bbb_phone="9545551234")]
    out = enrich_with_angi(rows, [])
    assert len(out) == 2
    assert all(r["on_angi"] == 0 for r in out)

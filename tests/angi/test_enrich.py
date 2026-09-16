"""bbb_scraper.angi.enrich -- phone-based matching of Angi records onto an
already-built master table. See tests/match/test_merge.py for the scoring
side (lead_priority_score etc. responding to angi_* fields)."""
from bbb_scraper.angi.enrich import enrich_with_angi


def _master_row(**overrides) -> dict:
    row = {"match_status": "bbb_only", "bbb_name": "Co", "bbb_phone": "(305) 555-0100", "bbb_rating": "B"}
    row.update(overrides)
    return row


def test_matches_by_exact_phone_and_fills_angi_fields():
    rows = [_master_row()]
    angi = [{"name": "Co on Angi", "phone": "3055550100", "overall_rating": "4.5",
             "review_count": "20", "categories": "Plumbing; Drain Cleaning"}]
    out = enrich_with_angi(rows, angi)
    assert out[0]["angi_name"] == "Co on Angi"
    assert out[0]["angi_overall_rating"] == "4.5"
    assert out[0]["angi_categories"] == "Plumbing; Drain Cleaning"
    assert out[0]["on_angi"] == 1


def test_phone_formatting_differences_still_match():
    """(305) 555-0100, 3055550100, and +1-305-555-0100 are the same phone."""
    rows = [_master_row(bbb_phone="+1 (305) 555-0100")]
    angi = [{"name": "Co", "phone": "305.555.0100"}]
    out = enrich_with_angi(rows, angi)
    assert out[0]["on_angi"] == 1


def test_no_matching_phone_leaves_angi_fields_blank():
    rows = [_master_row(bbb_phone="3055550100")]
    angi = [{"name": "Someone Else", "phone": "9545551234"}]
    out = enrich_with_angi(rows, angi)
    assert out[0]["angi_name"] == ""
    assert out[0]["on_angi"] == 0


def test_row_with_no_phone_at_all_is_left_unmatched_not_a_crash():
    rows = [_master_row(bbb_phone="")]
    angi = [{"name": "Co", "phone": "3055550100"}]
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


def test_many_master_rows_sharing_one_phone_all_get_the_same_match():
    rows = [_master_row(), _master_row()]
    angi = [{"name": "Co on Angi", "phone": "3055550100"}]
    out = enrich_with_angi(rows, angi)
    assert out[0]["angi_name"] == out[1]["angi_name"] == "Co on Angi"


def test_empty_angi_records_leaves_every_row_unmatched():
    rows = [_master_row(), _master_row(bbb_phone="9545551234")]
    out = enrich_with_angi(rows, [])
    assert all(r["on_angi"] == 0 for r in out)

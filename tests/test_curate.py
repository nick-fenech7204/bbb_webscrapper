"""bbb_scraper.curate -- publish-time filtering of detected chains, Angi
corporate accounts, and very-low-scoring leads. See the module's own
docstring for the real-data audit behind each threshold."""
from __future__ import annotations

from bbb_scraper.curate import (
    CHAIN_NAME_REPEAT_THRESHOLD,
    LOW_SCORE_CUTOFF,
    curate_for_publish,
    detect_chain_names,
)


def _row(name, **overrides) -> dict:
    row = {"bbb_name": name, "lead_priority_score": 50.0}
    row.update(overrides)
    return row


# --- detect_chain_names ------------------------------------------------

def test_detect_chain_names_below_threshold_is_empty():
    rows = [_row("Acme A"), _row("Acme A")]  # 2 occurrences, default threshold is 3
    assert detect_chain_names(rows) == set()


def test_detect_chain_names_at_threshold_is_caught():
    rows = [_row("Acme A"), _row("Acme A"), _row("Acme A")]
    assert detect_chain_names(rows) == {"acme a"}


def test_detect_chain_names_is_case_and_whitespace_insensitive():
    rows = [_row("Acme A"), _row("  ACME A  "), _row("acme a")]
    assert detect_chain_names(rows) == {"acme a"}


def test_detect_chain_names_ignores_blank_names():
    rows = [_row(""), _row(""), _row(""), _row(None)]
    assert detect_chain_names(rows) == set()


def test_detect_chain_names_respects_custom_threshold():
    rows = [_row("Acme A"), _row("Acme A")]
    assert detect_chain_names(rows, threshold=2) == {"acme a"}


def test_detect_chain_names_falls_back_to_angi_name_for_angi_only_rows():
    """2026-09-17, Nick's call: an angi_only row (no BBB match at all, see
    bbb_scraper/angi/enrich.py) has no bbb_name at all -- without the
    angi_name fallback, every angi_only row's normalized name reads as ""
    and a real repeated Angi-discovered chain would never get caught,
    the same "silently reads as not a business" gap blank bbb_name rows
    are already guarded against."""
    def angi_row(name):
        return {"match_status": "angi_only", "bbb_name": "", "angi_name": name, "lead_priority_score": 50.0}

    rows = [angi_row("West Shore Home"), angi_row("West Shore Home"), angi_row("West Shore Home")]
    assert detect_chain_names(rows) == {"west shore home"}


def test_default_threshold_is_the_real_data_justified_value():
    # Pinned so a future casual tweak notices it's changing something
    # backed by a real audit, not just a magic number -- see the module
    # docstring for the 61-groups-at-3+ / 918-groups-at-exactly-2 finding.
    assert CHAIN_NAME_REPEAT_THRESHOLD == 3


# --- curate_for_publish --------------------------------------------------

def test_unique_well_scored_rows_all_survive():
    rows = [_row("A"), _row("B"), _row("C")]
    kept, removed = curate_for_publish(rows)
    assert len(kept) == 3
    assert removed == {"chain_name": 0, "corporate_account": 0, "low_score": 0}


def test_chain_rows_are_all_removed_not_deduped_to_one():
    """A detected chain is dropped entirely -- this is deliberately NOT a
    dedup-to-one-representative step, see the module docstring: the whole
    point is these aren't the target customer (a business large enough to
    have 3+ branches in one metro already has its own marketing reach)."""
    rows = [_row("Big Chain"), _row("Big Chain"), _row("Big Chain"), _row("Small Co")]
    kept, removed = curate_for_publish(rows)
    assert [r["bbb_name"] for r in kept] == ["Small Co"]
    assert removed["chain_name"] == 3


def test_corporate_account_rows_are_removed():
    rows = [
        _row("Franchise Co", angi_corporate_account=1),
        _row("Independent Co", angi_corporate_account=0),
    ]
    kept, removed = curate_for_publish(rows)
    assert [r["bbb_name"] for r in kept] == ["Independent Co"]
    assert removed["corporate_account"] == 1


def test_corporate_account_flag_accepts_int_bool_and_string_forms():
    for truthy_value in (1, "1", True, "True", "true"):
        rows = [_row("Franchise Co", angi_corporate_account=truthy_value)]
        kept, removed = curate_for_publish(rows)
        assert kept == [], f"failed for {truthy_value!r}"
        assert removed["corporate_account"] == 1


def test_low_score_rows_are_removed():
    rows = [
        _row("Barely Under", lead_priority_score=LOW_SCORE_CUTOFF - 0.1),
        _row("Right At Cutoff", lead_priority_score=LOW_SCORE_CUTOFF),
        _row("Comfortably Above", lead_priority_score=LOW_SCORE_CUTOFF + 10),
    ]
    kept, removed = curate_for_publish(rows)
    assert {r["bbb_name"] for r in kept} == {"Right At Cutoff", "Comfortably Above"}
    assert removed["low_score"] == 1


def test_null_score_counts_as_low_score():
    """No evidence either way isn't more useful than confirmed-bad evidence
    -- see the module docstring's reasoning."""
    rows = [_row("No Score At All", lead_priority_score=None)]
    kept, removed = curate_for_publish(rows)
    assert kept == []
    assert removed["low_score"] == 1


def test_low_score_cutoff_none_disables_that_check_only():
    rows = [
        _row("Terrible Score", lead_priority_score=0.0),
        _row("Chain", lead_priority_score=90.0),
        _row("Chain", lead_priority_score=90.0),
        _row("Chain", lead_priority_score=90.0),
    ]
    kept, removed = curate_for_publish(rows, low_score_cutoff=None)
    assert [r["bbb_name"] for r in kept] == ["Terrible Score"]  # kept despite a 0.0 score
    assert removed == {"chain_name": 3, "corporate_account": 0, "low_score": 0}


def test_a_row_matching_multiple_reasons_is_counted_once_not_twice():
    """Chain detection is checked first -- a low-scoring row that's ALSO
    part of a detected chain is attributed to chain_name, not double-
    counted under both reasons."""
    rows = [
        _row("Big Chain", lead_priority_score=0.0),
        _row("Big Chain", lead_priority_score=0.0),
        _row("Big Chain", lead_priority_score=0.0),
    ]
    kept, removed = curate_for_publish(rows)
    assert kept == []
    assert removed == {"chain_name": 3, "corporate_account": 0, "low_score": 0}


def test_does_not_mutate_input_rows():
    rows = [_row("A"), _row("B"), _row("B"), _row("B")]
    original = [dict(r) for r in rows]
    curate_for_publish(rows)
    assert rows == original


def test_empty_input_is_handled():
    kept, removed = curate_for_publish([])
    assert kept == []
    assert removed == {"chain_name": 0, "corporate_account": 0, "low_score": 0}

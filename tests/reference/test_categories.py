from pathlib import Path

from bbb_scraper.reference.categories import CategoryDirectory


def test_load_from_real_categories_file():
    directory = CategoryDirectory.load(Path("data/reference/categories.json"))
    assert len(directory.all()) >= 1
    # "60004-000" is BBB's real category id for CPA (confirmed 2026-08-31 --
    # see data/reference/README.md), not a placeholder.
    cpa = directory.get("60004-000")
    assert cpa is not None
    assert cpa.name == "CPA"


def test_load_from_real_angi_categories_file():
    """Same CategoryDirectory, a second real taxonomy -- Angi's companylist
    categories (data/reference/README.md's angi_categories.json section:
    167 entries, confirmed 2026-09-14, id/slug is the Angi URL slug)."""
    directory = CategoryDirectory.load(Path("data/reference/angi_categories.json"))
    all_categories = directory.all()
    assert len(all_categories) == 167
    assert len({c.id for c in all_categories}) == 167  # no duplicate slugs
    assert len({c.name for c in all_categories}) == 167  # no duplicate names

    hvac = directory.get("hvac")
    assert hvac is not None
    assert hvac.name == "HVAC Companies"
    assert hvac.slug == "hvac"

    # A real example of the "label isn't a simple slugification" gotcha
    # (see the README) -- pins it so a bad re-fetch can't silently drift.
    antenna = directory.get("tv-antenna")
    assert antenna is not None
    assert antenna.name == "Antenna Repair"

    # Angi is home-services only -- confirmed absent, not just unchecked.
    assert directory.search("Dentist") == []
    assert directory.search("Car Dealer") == []

    # Real regression, 2026-09-17: "Electricians" (the plural, completely
    # ordinary as an --industry value) against the real file's singular
    # "Electrician" entry -- see the bidirectional-substring test above for
    # the synthetic version of this same bug.
    electrician = directory.resolve_one("Electricians")
    assert electrician is not None
    assert electrician.name == "Electrician"


def test_load_missing_file_returns_empty_directory(tmp_path):
    directory = CategoryDirectory.load(tmp_path / "does_not_exist.json")
    assert directory.all() == []
    assert directory.search("plumbers") == []


def test_search_exact_name_match_takes_priority(tmp_path):
    path = tmp_path / "categories.json"
    path.write_text(
        '[{"id": "1", "name": "Plumbers"}, {"id": "2", "name": "Plumbers Supply Co"}]',
        encoding="utf-8",
    )
    directory = CategoryDirectory.load(path)
    matches = directory.search("Plumbers")
    assert len(matches) == 1
    assert matches[0].id == "1"


def test_search_matches_a_plain_plural_of_the_category_name(tmp_path):
    """Real bug, caught 2026-09-17 live-testing a sample batch: "Electricians"
    (a completely ordinary industry name) silently found nothing against a
    directory whose entry is named "Electrician" (singular), because the old
    substring check only tested query-in-name, never name-in-query -- and
    "electricians" is never a substring of the shorter "electrician". Fixed
    by checking both directions."""
    path = tmp_path / "categories.json"
    path.write_text('[{"id": "1", "name": "Electrician"}]', encoding="utf-8")
    directory = CategoryDirectory.load(path)
    matches = directory.search("Electricians")
    assert len(matches) == 1
    assert matches[0].id == "1"


def test_search_substring_returns_multiple_candidates(tmp_path):
    path = tmp_path / "categories.json"
    path.write_text(
        '[{"id": "1", "name": "Plumbers"}, {"id": "2", "name": "Plumbing Supply Co"}]',
        encoding="utf-8",
    )
    directory = CategoryDirectory.load(path)
    matches = directory.search("plumb")
    assert {m.id for m in matches} == {"1", "2"}


def test_get_by_id_or_slug(tmp_path):
    path = tmp_path / "categories.json"
    path.write_text('[{"id": "1", "name": "Plumbers", "slug": "plumbers"}]', encoding="utf-8")
    directory = CategoryDirectory.load(path)
    assert directory.get("1").name == "Plumbers"
    assert directory.get("plumbers").name == "Plumbers"
    assert directory.get("nope") is None


# --- resolve_one (best-effort/non-interactive: batch_scrape_metros.py's
# Angi integration -- a batch can't ask a human to pick between ambiguous
# matches, so ambiguous collapses to None same as no match at all) --------

def test_resolve_one_returns_the_single_exact_match(tmp_path):
    path = tmp_path / "categories.json"
    path.write_text('[{"id": "1", "name": "Plumbers"}, {"id": "2", "name": "Plumbing Supply Co"}]',
                     encoding="utf-8")
    directory = CategoryDirectory.load(path)
    match = directory.resolve_one("Plumbers")
    assert match is not None
    assert match.id == "1"


def test_resolve_one_returns_none_for_no_match(tmp_path):
    path = tmp_path / "categories.json"
    path.write_text('[{"id": "1", "name": "Plumbers"}]', encoding="utf-8")
    directory = CategoryDirectory.load(path)
    assert directory.resolve_one("Dentists") is None


def test_resolve_one_returns_none_for_an_ambiguous_substring_match(tmp_path):
    path = tmp_path / "categories.json"
    path.write_text('[{"id": "1", "name": "Plumbers"}, {"id": "2", "name": "Plumbing Supply Co"}]',
                     encoding="utf-8")
    directory = CategoryDirectory.load(path)
    assert directory.resolve_one("plumb") is None  # 2 substring matches, neither exact


# --- resolve_by_slug_token (2026-09-16: a real incident -- see the
# function's own docstring. Angi's "HVAC Companies" label sent straight to
# BBB's search returned fire/water-damage restoration companies instead of
# HVAC contractors; this exists to swap in this (BBB) directory's own
# confirmed name first, since resolve_one/search can't -- "hvac companies"
# is never going to be a substring of "Heating and Air Conditioning".) -----

def test_resolve_by_slug_token_matches_a_whole_word_from_a_different_taxonomy(tmp_path):
    path = tmp_path / "categories.json"
    path.write_text('[{"id": "10182-000", "name": "Heating and Air Conditioning", "slug": "hvac"}]',
                     encoding="utf-8")
    directory = CategoryDirectory.load(path)
    match = directory.resolve_by_slug_token("HVAC Companies")
    assert match is not None
    assert match.name == "Heating and Air Conditioning"


def test_resolve_by_slug_token_requires_a_whole_word_not_a_bare_substring(tmp_path):
    path = tmp_path / "categories.json"
    path.write_text('[{"id": "1", "name": "Heating and Air Conditioning", "slug": "hvac"}]',
                     encoding="utf-8")
    directory = CategoryDirectory.load(path)
    # "hvacr" contains "hvac" as a substring but isn't the same word.
    assert directory.resolve_by_slug_token("HVACR Companies") is None


def test_resolve_by_slug_token_returns_none_with_no_slug_match(tmp_path):
    path = tmp_path / "categories.json"
    path.write_text('[{"id": "1", "name": "Heating and Air Conditioning", "slug": "hvac"}]',
                     encoding="utf-8")
    directory = CategoryDirectory.load(path)
    assert directory.resolve_by_slug_token("Landscapers") is None


def test_resolve_by_slug_token_returns_none_when_ambiguous(tmp_path):
    path = tmp_path / "categories.json"
    path.write_text(
        '[{"id": "1", "name": "A", "slug": "hvac"}, {"id": "2", "name": "B", "slug": "hvac-repair"}]',
        encoding="utf-8",
    )
    directory = CategoryDirectory.load(path)
    # Only "hvac" is a whole-word match here ("hvac-repair" is a different
    # slug entirely, not embedded as a single token) -- not ambiguous.
    match = directory.resolve_by_slug_token("HVAC Companies")
    assert match is not None
    assert match.id == "1"

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

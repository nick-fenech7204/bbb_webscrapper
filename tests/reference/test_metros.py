from pathlib import Path

from bbb_scraper.reference.metros import MetroDirectory


def test_load_missing_file_returns_empty_directory(tmp_path):
    directory = MetroDirectory.load(tmp_path / "does_not_exist.json")
    assert directory.all() == []
    assert directory.get("miami-fl") is None


def test_load_from_real_metros_file():
    directory = MetroDirectory.load(Path("data/reference/metros.json"))
    assert len(directory.all()) >= 10

    miami = directory.get("miami-fl")
    assert miami is not None
    assert miami.seed_location == "Miami, FL"


def test_get_returns_none_for_unknown_id(tmp_path):
    path = tmp_path / "metros.json"
    path.write_text(
        '[{"id": "miami-fl", "name": "Miami, FL", "seed_location": "Miami, FL"}]',
        encoding="utf-8",
    )
    directory = MetroDirectory.load(path)
    assert directory.get("nope") is None
    assert directory.get("miami-fl").name == "Miami, FL"

"""fetch_angi_categories's HTML parsing, in isolation -- a small real-shaped
fixture, no network. Same sys.path pattern as tests/test_publish_site_data.py
for reaching into scripts/."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from fetch_angi_categories import _parse_categories_page

# Trimmed but real-shaped: a handful of genuine label/slug pairs (including
# the "label isn't a simple slugification" case, tv-antenna.htm ==
# "Antenna Repair") plus noise a real page also has -- an unrelated link,
# a duplicate category link (real pages repeat some links in a footer
# "related services" section), and a same-shaped href with no visible text.
_SAMPLE_HTML = """
<html><body>
<nav><a href="https://angi.com">Angi</a></nav>
<ul>
  <li><a href="air-duct-cleaning.htm">Air Duct Cleaning</a></li>
  <li><a href="tv-antenna.htm">Antenna Repair</a></li>
  <li><a href="hvac.htm">HVAC Companies</a></li>
</ul>
<footer>
  <a href="air-duct-cleaning.htm">Air Duct Cleaning</a>
  <a href="/companylist/us/ny/albany/">Albany</a>
  <a href="weird.htm"></a>
</footer>
</body></html>
"""


def test_parses_label_slug_pairs():
    categories = _parse_categories_page(_SAMPLE_HTML)
    by_slug = {c["slug"]: c["name"] for c in categories}
    assert by_slug == {
        "air-duct-cleaning": "Air Duct Cleaning",
        "tv-antenna": "Antenna Repair",
        "hvac": "HVAC Companies",
    }


def test_dedupes_repeated_category_links():
    categories = _parse_categories_page(_SAMPLE_HTML)
    assert len(categories) == 3  # air-duct-cleaning only once despite 2 links


def test_skips_non_category_and_empty_label_links():
    categories = _parse_categories_page(_SAMPLE_HTML)
    slugs = {c["slug"] for c in categories}
    assert "weird" not in slugs  # href matches .htm shape but has no text


def test_sorted_by_name():
    categories = _parse_categories_page(_SAMPLE_HTML)
    names = [c["name"] for c in categories]
    assert names == sorted(names)


def test_each_entry_has_id_matching_slug():
    for c in _parse_categories_page(_SAMPLE_HTML):
        assert c["id"] == c["slug"]

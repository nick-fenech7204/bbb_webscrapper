# categories.json

BBB's industry/category taxonomy, used to drive category-based search.

**Confirmed 2026-08-31 against a real captured search request:** BBB's
search API doesn't take a category id -- it takes `find_text` (the category
*name*, e.g. `"accredited cpa"`) plus a static `find_type=Category`. So
`Category.name` is what actually goes on the wire (see
`bbb_scraper/scraping/search.py`); `id` here is purely an internal lookup
key. It's not arbitrary though -- see below.

**`categories.json` currently holds 10 real entries**, not placeholders --
`id`/`name` pairs pulled directly from a real response's category filter
options (`filters.byId.filter_category.filterOptions`, which every
`/api/search` response includes, scoped to categories related to that
particular query). Because the seed query was "accredited cpa", all 10 are
finance/accounting-related -- this is a real but narrow slice, not the full
taxonomy across every industry BBB covers.

One entry (`hvac`) is different: a best-guess `name` ("Heating and Air
Conditioning") added to unblock a pilot in a new vertical, not yet confirmed
against a real response the way the other 10 are -- flagged by using a
plain word as its `id` instead of a real harvested `NNNNN-NNN`-style BBB id,
so it's visually obvious which entries are confirmed vs. guessed. The first
real search against it will confirm quickly whether the phrasing returns
good matches; if BBB's `filters.filter_category.filterOptions` on that
response has a better/more precise name, swap it in and give it the real
harvested id at the same time.

The `id` values (e.g. `"60004-000"` for CPA) are BBB's own category ids --
they show up as `tobId` on every search result and as `categories[].id` too
(see `tests/fixtures/search_listing_sample.json`). They're not sent in the
request, but they're real and stable, so they're worth keeping around: you
can cross-reference a result's `tobId`/`categories` against this file, and a
future geocoding/enrichment step may find other uses for a real id instead
of an invented slug.

## Getting the full list

The per-query filter options above only ever give you categories related to
whatever you already searched for -- not a way to enumerate every category
BBB has. To get the full taxonomy you need a different source (a directory/
browse page, or another endpoint) -- once you've found it:

1. Fill in `scripts/fetch_categories.py`'s `_parse_categories_page`.
2. Run it:
   ```bash
   python scripts/fetch_categories.py
   ```
   which overwrites `data/reference/categories.json`.

Or grow it incrementally: every real search response's `filters` /
`relatedCategories` / `mostPopularCategories` is another small batch of real
categories (some of the latter two only have `title`/`url`, no numeric
`id` -- see a captured response for the shape) -- worth harvesting as you go
even before `fetch_categories.py` exists.

## Schema

```json
{ "id": "60004-000", "name": "CPA", "slug": "cpa" }
```

- `id` -- required. BBB's own category id where known; otherwise any stable
  internal key.
- `name` -- required. Sent as `find_text` in the search request, so this
  needs to be a phrase BBB actually recognizes -- not just a display label.
- `slug` -- optional, informational only (not used in the request).

# us_cities.csv

Every US incorporated place *and* census-designated place (CDP), with real
lat/lon and 2020 Census population. Powers metro coverage search (see
`bbb_scraper/reference/cities.py` and the README's "Metro coverage search"
section) -- finding real named nearby cities to sweep, not just
mathematical points.

Built by `python scripts/build_us_cities.py` (needs a free `CENSUS_API_KEY`
in `.env` -- see that script's module docstring for the full reasoning, in
short: neither Census's own annual population estimates nor SimpleMaps'
free cities database cover CDPs at all, only the one-time 2020 Decennial
count does). Not auto-regenerated -- re-run by hand occasionally (Census
updates its source data roughly yearly).

## Schema

```csv
name,state,lat,lon,population,geoid
Kendall,FL,25.669538,-80.354741,80241,1236100
```

- `name` -- place name with its Census LSAD suffix stripped (`"Kendall CDP"`
  -> `"Kendall"`) so it's ready to use as-is in a `find_loc`-style search.
- `state` -- two-letter USPS code.
- `lat`/`lon` -- decimal degrees (Census Gazetteer's internal point).
- `population` -- 2020 Decennial Census total population (not an estimate).
- `geoid` -- Census's own 7-digit place identifier (2-digit state FIPS +
  5-digit place FIPS), kept for traceability back to the source data.

# metros.json

A small, hand-picked list of major US metros for the metro-coverage
dropdown (CLI's `--metro`, Streamlit's "Metro sweep" mode) -- not every
place in `us_cities.csv`, just the ones worth offering as a "sweep this
whole metro" starting point. Extend it by adding more entries in the same
shape; no code changes needed.

## Schema

```json
{ "id": "miami-fl", "name": "Miami, FL", "seed_location": "Miami, FL" }
```

- `id` -- required, stable key (what `--metro` matches against).
- `name` -- required, display label (dropdown text).
- `seed_location` -- required. Fed through `parse_location` and searched
  first to resolve BBB's own center point for the metro -- must be a real
  "City, ST" BBB can resolve via `find_loc`, not necessarily the metro's
  official/full statistical-area name.

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

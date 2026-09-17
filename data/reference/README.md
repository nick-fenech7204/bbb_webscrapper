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

One entry, HVAC, started as a best-guess `name` ("Heating and Air
Conditioning") added to unblock a pilot in a new vertical, flagged with a
plain-word `id` ("hvac") instead of a real harvested `NNNNN-NNN`-style BBB
id to make clear it hadn't been confirmed yet.

**Confirmed live, 2026-09-16** (id upgraded to the real harvested
`10182-000`, `filters.byId.filter_category.filterOptions` on that response
lists it as `{"value": "10182-000", "label": "Heating and Air
Conditioning"}`) -- the guessed phrasing was right: searching it returns
real HVAC contractors, 4882 results for Los Angeles, CA. This was checked
specifically because a real batch run sent a DIFFERENT phrase, "HVAC
Companies" (Angi's own category label -- see "Getting the industry name
right" below), straight to BBB instead of this entry's confirmed one, and
BBB's own full-text search for that exact phrase returns fire/water-damage
restoration companies as its top results (146 total, SERVPRO first, real
HVAC contractors nowhere near the top) -- confirmed by re-running the same
search with each phrase side by side. A whole 5-metro batch came back the
wrong industry before this was caught.

## Getting the industry name right for BOTH BBB and Angi

The Streamlit batch page's one `--industry` field is seeded from Angi's own
category dropdown (`angi_categories.json`, 167 labels) and sends that exact
text to BOTH systems -- Angi resolves it against its own directory first,
but BBB gets it as literal free text, on the long-standing assumption
(recorded below, in the angi_categories.json section) that "BBB's search
takes any phrase." **That assumption is not reliable** -- BBB's own search
relevance can silently return a wrong industry for a phrase that isn't how
BBB itself names that category, with no error, just quietly bad data (this
is exactly what happened with "HVAC Companies" above). `bbb_scraper.reference.
categories.CategoryDirectory.resolve_by_slug_token` exists to catch the
narrow case where this file's own slug (a short, single word like "hvac")
appears as a whole word inside the Angi-style label -- scripts/
batch_scrape_metros.py uses this to swap in this file's confirmed `name`
before searching BBB, still falling back to the literal `--industry` text
whenever nothing here matches (true for most industries -- this file only
has 11 entries, not BBB's full taxonomy). It is NOT a substitute for
actually growing this file with more real, confirmed categories as new
industries get piloted.

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

# angi_categories.json

Angi's full companylist category taxonomy -- **167 entries**, confirmed
2026-09-14 by rendering a real city hub page (e.g.
`https://www.angi.com/companylist/us/ny/albany/`) and reading its "Top
categories" links. Loaded via the same `CategoryDirectory` class as
`categories.json` above (`CategoryDirectory.load(settings.angi_categories_file)`)
-- the schema is identical, so nothing new was needed to query it.

Seeds the Streamlit batch app's industry picker (a dropdown of these 167
names, `accept_new_options=True` so typing something else still works):
picking one from the list guarantees it also resolves as a real Angi
category once Angi matching is built, vs. typing free text, which is
guaranteed to work for BBB (its search takes any phrase) but not
necessarily for Angi.

**Angi is a home-services marketplace, not a general local-business
directory like BBB** -- confirmed by checking the raw category page
directly: no "Dentist"/"Dental" entry, no "Car Deal(ers)"/"Auto Deal(ers)"
entry, anywhere in the 167. "Real Estate Agent" is a real entry, most of
the rest are home-improvement/home-maintenance trades (plumbing,
electrical, roofing, landscaping, HVAC, cleaning, remodeling, ...). This
list is not a substitute for BBB's own (much broader) taxonomy -- it's a
second, narrower one for the categories where cross-referencing Angi is
actually possible.

**The label shown on the page is often not a simple slugification of the
URL slug** -- confirmed on real examples, not assumed: "Antenna Repair" is
`tv-antenna.htm`, "Basement Remodeling" is `remodeling-basements.htm`,
"Garage Building" is `garage-builders.htm`, "HVAC Companies" is `hvac.htm`.
That's why this file exists as harvested label/slug *pairs* rather than
something a caller derives from the label at request time.

Confirmed global/canonical, not per-city: the same label/slug pairs showed
up on two unrelated cities' hub pages (Lansing, NY and Albany, NY) --
there's one taxonomy, not one per city, so any real city's hub page is a
valid source to (re-)harvest it from.

Built by `python scripts/fetch_angi_categories.py` (no API key or proxy
needed -- a single plain request, same as `metros.json`, not
auto-regenerated; re-run by hand if Angi adds/renames/removes a category).
Raw HTML for each fetch is captured under `data/raw/angi_categories/` first,
same "save the raw payload before parsing" pattern as every other scraped
page in this project.

## Schema

```json
{ "id": "hvac", "name": "HVAC Companies", "slug": "hvac" }
```

- `id` -- the Angi URL slug, reused as the stable lookup key (unlike
  `categories.json`, there's no separate internal id scheme here -- the
  slug already is one).
- `name` -- the display label shown on Angi's own category link. Not sent
  in any request; what a person picks in the dropdown.
- `slug` -- same value as `id`, kept as its own field because a caller
  building a URL (`.../{slug}.htm`) wants it semantically, not because it
  ever differs from `id` for this file.

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

# BBB Scraper + ETL Pipeline

A framework for scraping BBB (Better Business Bureau) business listings and
profile pages, normalizing the data, and loading it into a configurable set
of destinations (CSV, SQL, Excel, an enrichment API, ...).

## Why it's laid out this way

The four layers are deliberately decoupled so each can be developed/tested
independently:

```
scraping/   -> talks to BBB over HTTP (proxying, retries, rate limiting).
               Knows nothing about what the HTML/JSON means.
parsing/    -> turns raw HTML into normalized Python objects
               (BusinessSummary, BusinessDetail). Knows nothing about HTTP.
               Runs entirely against saved fixtures -- no network needed.
etl/        -> Extract (glues scraping+parsing) -> Transform (pure
               functions, dict out) -> Dedupe -> Load.
pipeline/   -> the Sink interface + concrete destinations (CSV, SQL, Excel,
               JSON, HTTP/webhook, null). The ETL layer only ever talks to
               `Sink.load()`, never to a specific destination -- so the
               eventual "report / database / enrichment / other app"
               decision doesn't require touching the scraper at all.
```

```
bbb_scraper/
  config.py            # all settings, sourced from env vars / .env
  logging_setup.py      # console + rotating file logging
  exceptions.py
  reference/
    models.py              # Category, Location, City, Metro (+ parse_location)
    categories.py            # CategoryDirectory: load/search data/reference/categories.json
    cities.py                  # CityDirectory: load/query data/reference/us_cities.csv
                                #   (every US place + CDP, real population -- powers area/metro sweep)
    metros.py                    # MetroDirectory: load/query data/reference/metros.json
                                    #   (curated ~50-metro list for --metro / Batch Scraper)
    geo.py                          # dependency-free great-circle math (destination_point,
                                     #   distance_miles, generate_coverage_points)
  scraping/
    client.py           # HttpClient: curl_cffi (browser TLS impersonation) + proxy + retry + rate limit
    proxies.py           # provider-agnostic proxy URL construction (Decodo, IPRoyal, ...)
    session.py            # loads BBB session cookies/headers from data/secrets/ (optional, not required)
    capture.py              # save every raw response to data/raw/ + manifest.jsonl
    search.py                # BBB /api/search JSON requests, by Category + Location
    business.py                # BBB business-profile page (HTML) requests
  parsing/
    json_extract.py       # generic: <script type=application/json>, window.X = {...}
    models.py              # BusinessSummary, BusinessDetail (pydantic)
    search_parser.py        # /api/search JSON -> list[BusinessSummary] (BBB-specific mapping)
    business_parser.py       # profile HTML -> BusinessDetail          (BBB-specific mapping)
  etl/
    identifiers.py         # stable internal business id
    transform.py            # pure functions: model -> flat dict
    dedupe.py                 # dedupe by id
    extract.py                 # Extractor: extract_search, extract_search_coverage (lat/lon
                                #   ring sweep), extract_search_area_coverage (real-nearby-city
                                #   sweep, any place), extract_search_metro_coverage (thin wrapper
                                #   over the above for the curated metros.json list), extract_business
    pipeline.py                 # ETLPipeline.run_search: plain / coverage=True / metro=<Metro> modes
  pipeline/
    base.py                # Sink ABC
    registry.py              # OUTPUT_SINKS env var -> list[Sink]
    sinks/                     # csv, excel, sql, json, http, null
  utils/
    rate_limit.py, hashing.py, stats.py, flatten.py

data/
  reference/
    categories.json        # BBB industry/category taxonomy (11 entries, mostly real -- see its README)
    us_cities.csv             # every US incorporated place + CDP, real lat/lon + 2020 Census
                               #   population (scripts/build_us_cities.py builds this)
    metros.json                  # curated major-metro list for --metro / the Batch Scraper page
  secrets/                        # gitignored -- bbb_session.json (optional, not required)
  processed/                        # gitignored -- businesses.csv is the one cumulative sink every
                                     #   run appends to; batch/ holds the batch scraper's per-metro
                                     #   checkpoints (resumability)
  raw/                                # gitignored -- every raw response saved + manifest.jsonl

tests/
  fixtures/                 # saved HTML/JSON used by parser unit tests
  parsing/                    # parser tests (no network)
  etl/                          # transform/dedupe tests (no network)
  reference/                     # Category/Location/City/Metro/geo tests
  scraping/                        # search param-building + proxy + client tests
  pipeline/                          # CSVSink tests

scripts/
  run_search.py             # CLI: category + location -> ETL -> sinks (plain / --coverage / --metro)
  run_business.py             # CLI: fetch + parse one profile page
  fetch_details.py            # CLI: enrich an existing CSV's profile_urls, no re-search needed
  fetch_categories.py           # CLI stub: scrape BBB's category taxonomy into data/reference/
  check_proxy.py                 # CLI: verify the configured proxy actually works
  build_us_cities.py               # one-time reference-data build (needs a free CENSUS_API_KEY)
  batch_scrape_metros.py             # run one industry across many metros, checkpointed +
                                      #   auto-published to site/ as each one finishes
  publish_site_data.py                 # CSV (or in-memory records) -> site/data/*.json + manifest.json
  deploy_site.py                         # aws s3 sync + cloudfront invalidation (site/DEPLOY.md)

streamlit_app.py           # main control-panel UI: one unified search flow (see "UI" below)
pages/
  1_Batch_Scraper.py          # Streamlit multi-page: run many metros in the background, live log

site/                      # the genuinely static public site (S3+CloudFront) -- see "Static site"
                            #   below; a different thing from streamlit_app.py on purpose

run_streamlit.bat, open_cli.bat, deploy_site.bat   # double-click launchers (Windows) --
                                                    #   activate the venv and run the thing named
```

## Searching by category + location

Search is driven by a `Category` (from BBB's own industry taxonomy, not free
text) and a `Location` (ZIP code or city/state) -- see
`bbb_scraper/reference/`. This maps directly onto BBB's own filters instead
of hoping a text search lands on the right vertical.

```bash
# interactive: prompts for category (with keyword search + disambiguation) and location
python scripts/run_search.py

# non-interactive
python scripts/run_search.py --category plumbers --location "Austin, TX" --pages 2

# discovery: see what's available
python scripts/run_search.py --list-categories plumb
```

`--category` accepts an id, slug, exact name, or partial name; ambiguous
partial matches print the candidates and exit rather than guessing.

## Coverage search (multiple locations within a radius)

BBB's location search does **not** actually scope results to the area
around `find_loc`/`find_latlng` -- confirmed empirically (2026-09-02): the
same city/category search from four different Seattle-metro cities (Seattle,
Tacoma, Bellevue, Federal Way) all returned nearly the same statewide pool of
~230 businesses spanning 91 WA cities, some 140+ miles apart. A single search
against "Miami, FL" is really a search of Florida (or wider), not a 25-mile
radius around Miami.

`--coverage` works around this by sweeping several search anchors -- not
just one -- around the requested location:

```bash
python scripts/run_search.py --category cpa --location "Miami, FL" \
    --coverage --radius 25 --num-points 16 --max-pages-per-point 2
```

How it works, concretely:

1. The first search against `--location` also reads BBB's own resolved
   center coordinates back out of the response (`location.latLng` --
   populated for any name-based `find_loc` search). No geocoding library or
   API call needed; BBB already did that lookup to serve the first request.
2. `bbb_scraper/reference/geo.py` scatters `--num-points` anchor points in
   rings around that center, out to `--radius` miles, using plain great-circle
   math (no dependency).
3. Each anchor is searched with `sort=Distance` (confirmed working
   2026-09-02), so its first page or two -- `--max-pages-per-point`,
   deliberately low by default -- surfaces businesses actually near *that*
   anchor rather than BBB's usual best-match ordering.
4. All anchors' results are pooled and deduped exactly like a normal search
   (`etl/dedupe.py`) -- the same business turning up under several nearby
   anchors is expected, not a bug.

`--pages` is ignored in coverage mode; use `--max-pages-per-point` instead.
`--location` should be a city/state or ZIP (something BBB can geocode by
name), not raw lat/lon -- coverage mode needs that first response's resolved
center. The same options are available programmatically via
`ETLPipeline.run_search(..., coverage=True, radius_miles=..., num_points=...,
max_pages_per_point=...)`, and at a lower level via
`Extractor.extract_search_coverage()` if you want the raw, undeduped
summaries. CLI-only now -- the main Streamlit search page uses metro-style
sweeping (below) for every place automatically instead.

Coverage mode multiplies request count by roughly `num_points *
max_pages_per_point` -- keep both modest (the defaults above: 16 points x 2
pages = up to 32 requests) rather than maxing them out; see
[Session cookies](#session-cookies-optional-not-required) below for the
general spirit of not hammering BBB harder than a real user would.

**Important limitation, confirmed 2026-09-02:** coverage mode's lat/lon
anchors don't actually reach full metro coverage the way they might sound
like they would. A real test swept 20 points up to 15 miles from downtown
Miami for "Car Dealers" -- every genuinely local result still clustered
within 6.75 miles of the *center*, regardless of how far out an anchor sat;
the next-nearest result after that jumped straight to 186 miles away. Then,
searching the real named place "Kendall, FL" directly (a real Miami-Dade
suburb about 13 miles from downtown) surfaced 15 completely different real
local businesses a lat/lon point at that same distance never found. BBB's
"local" result pool is apparently tied to the specific *named place*
searched (`find_loc`), not just proximity to a point (`find_latlng`) --
coordinates and place names draw from meaningfully different pools, not
just different sort orders of the same one. **For genuine full-metro
coverage, use metro coverage search (below) instead** -- coverage mode above
still has its place for a quick sweep around a single unnamed point.

## Area/metro coverage search (sweeping real cities around any place)

Works around the limitation above by searching real, named nearby
cities/CDPs instead of mathematical points -- confirmed to reach local
results plain coverage search cannot. This is what the main Streamlit
search page uses for every place automatically (see UI section below) --
the CLI form below is the same thing, explicit and scriptable.

```bash
python scripts/run_search.py --category "Car Dealers" --metro miami-fl \
    --metro-radius 40 --metro-min-population 25000
python scripts/run_search.py --list-metros   # see available --metro ids
```

How it works:

1. The seed place is searched first, both for its own results and to
   resolve BBB's own center coordinates for it (`location.latLng`, same
   free mechanism coverage search uses).
2. `data/reference/us_cities.csv` -- every incorporated place *and*
   census-designated place (CDP) in the US, with real lat/lon and 2020
   Census population, built by `scripts/build_us_cities.py` -- is filtered
   to real places within the radius of that center, at or above the
   population floor (25,000 by default; without a floor, a 40-mile radius
   around a big metro can catch 100+ tiny places, multiplying request
   count far past what's useful).
3. Each matching place is searched by name (`find_loc`) in full, same as
   the seed place -- not the shallow, capped depth coverage search's
   anchors use, since each is a real named search in its own right, not an
   arbitrary nearby point.
4. All results are pooled and deduped exactly like any other search.

Two entry points, same underlying logic (confirmed 2026-09-04 this
generalizes fine -- a small town like "Palm Coast, FL" gets exactly the
same treatment, just naturally sweeps fewer or zero extra places if
nothing substantial is genuinely nearby):

- **`Extractor.extract_search_area_coverage(category, location, ...)`** --
  the general version, takes any `Location` (typed free text, resolved by
  BBB). This is what the main Streamlit page and `ETLPipeline.run_search`
  use.
- **`Extractor.extract_search_metro_coverage(category, metro, ...)`** -- a
  thin wrapper over the above for the curated `data/reference/metros.json`
  list specifically (the CLI's `--metro` flag above, and the Batch
  Scraper's multi-metro picker) -- useful when you want a fixed, known-good
  list of major metros to iterate rather than typing places freely.

**Regenerating `us_cities.csv`:** only needed occasionally (Census updates
its data roughly yearly) -- `python scripts/build_us_cities.py` needs a free
Census API key (`CENSUS_API_KEY` in `.env`,
[sign up here](https://api.census.gov/data/key_signup.html), no cost). Why
not a simpler population source: Census's own annual estimates (SUB-EST)
and SimpleMaps' free cities database were both checked and both exclude
CDPs/unincorporated places entirely -- exactly the kind of place (Kendall,
Olympia Heights) this feature most needs. Only the 2020 Decennial Census
count covers every place the Gazetteer geography file does.

Metro sweep's request count is roughly `(nearby places found) *
metro_max_pages_per_place` -- easily 200-300+ for a big metro at the default
floor, so the same politeness-delay guidance in
[Session cookies](#session-cookies-optional-not-required) applies here even
more than to plain coverage search.

## UI

A [Streamlit](https://streamlit.io) control panel (`streamlit_app.py`) for
the same searches, if you'd rather use a form than the CLI. Revamped
2026-09-04 to one simple flow, no mode picker: type an industry/category
phrase (free text -- BBB's search takes `find_text` directly and accepts a
wide range of phrasing) and one or more places, one per line -- *any*
resolvable place, a major metro or a small town alike (e.g. "Palm Coast,
FL"), not limited to a curated list. Every place gets
`Extractor.extract_search_area_coverage` automatically: swept together with
real nearby towns at fixed, proven settings (40mi radius, 25,000+
population, full page depth -- no longer exposed as knobs, see that
method's docstring for why these particular numbers), so a big metro
sweeps wide and a small town with nothing substantial nearby just searches
itself -- no separate decision needed either way. A live progress line
shows exactly which place is being searched and a running result count,
not just a single bar that jumps at the very end. Optionally fetch full
details, then get a sortable table plus a CSV download in the browser.
It's a thin presentation layer over the exact same
`Extractor`/`transform`/`dedupe`/sinks everything else uses -- no separate
scraping or parsing logic lives in it. (The lat/lon ring sweep and the
curated-metro-list mode described above are still available -- CLI only
now, and the curated metro list still drives the separate Batch Scraper
page's multi-metro picker, see below -- just not exposed on this page
anymore.)

```bash
streamlit run streamlit_app.py
```

**Deploying it:** push to GitHub (already set up), connect the repo at
[share.streamlit.io](https://share.streamlit.io) pointed at
`streamlit_app.py` -- free hosting, and that's the default filename their
auto-deploy looks for. You'll need to set `PROXY_*`/`HTTP_IMPERSONATE`/etc.
via Streamlit's own Secrets manager there instead of a committed `.env`
(same values, different mechanism -- `.env` never gets deployed, it's
gitignored).

**Before deploying it somewhere public, read this:** this page *is* a live
backend, not a static site -- clicking "Run search" makes real requests
through your real proxy using your real BBB session. It's a different thing
from the "cheap static public insight site" discussed separately (that one
reads pre-generated data files with no live scraping involved). A publicly
reachable "run scraper" button tied to your proxy account with no auth in
front of it is a real cost/abuse risk -- there's none built into this app.
Keep it private, or put real authentication in front of it, before treating
"deployed" as "public."

## Static site

The "cheap static public insight site" referenced above -- a genuinely
different thing from the Streamlit control panel, on purpose (decided
2026-09-02). Plain HTML/CSS/JS in `site/`, no build step, no backend: it
only ever reads pre-published data files, never scrapes live. That's the
whole safety story -- there's no publicly reachable path to your proxy or
BBB session, so there's nothing to lock down or rate-limit.

```bash
# publish a dataset (after a pipeline run produces a CSV)
python scripts/publish_site_data.py data/processed/miami_car_dealers_full.csv \
    --industry "Car Dealers" --metro "Miami, FL"

# preview locally
python -m http.server 8502 --directory site   # then open localhost:8502
```

See [site/README.md](site/README.md) for the full structure and how
publishing works, and [site/DEPLOY.md](site/DEPLOY.md) for a step-by-step
AWS console walkthrough (S3 static hosting behind CloudFront -- cheap,
near-$0/month at low traffic, the standard pattern for exactly this kind of
site). **Live as of 2026-09-02** (Nick deployed it himself via the console,
per DEPLOY.md). Publishing an update after that first deploy is
`python scripts/deploy_site.py` (or double-click `deploy_site.bat`) --
syncs `site/` to S3 and invalidates CloudFront in one step; see
DEPLOY.md's "Automating updates" section for the one-time AWS CLI setup
it needs.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev,excel,sql,ui]"
copy .env.example .env
```

Fill in `.env` with your proxy details (`PROXY_HOST`, `PROXY_PORT`, and
`PROXY_USERNAME`/`PROXY_PASSWORD` if your provider needs them -- leave those
two blank if you're using IP whitelisting instead). The config is
provider-agnostic: works with Decodo, IPRoyal, or anything else that hands
out plain `user:pass@host:port`, no code changes needed to switch providers.

For Decodo specifically: country targeting is done via **hostname**, e.g.
`PROXY_HOST=us.decodo.com` for a US exit node vs. `gate.decodo.com` for
undirected/random-country (confirmed 2026-08-31 -- see
`bbb_scraper/scraping/proxies.py`).

Then check it's actually working -- this hits an IP-check endpoint through
the configured proxy and prints where it thinks you're coming from:

```bash
python scripts/check_proxy.py
```

Nothing secret lives in the repo -- `config.py` reads everything from
environment variables / `.env`, and `.env` is gitignored.

## Running tests

```bash
pytest
```

Parser tests run entirely against the fixtures in `tests/fixtures/` -- no
network access, no proxies needed. This is intentional: parser development
should never require hitting BBB.

## What's a placeholder vs. what's real

**Confirmed real, end to end, against a live request (2026-08-31):** search
is a JSON API, not an HTML page -- `GET https://www.bbb.org/api/search` with
`find_country`, `find_text` (the category phrase itself, e.g. "accredited
cpa" -- there's no separate opaque category id in the request), `find_type=
Category` (always this exact value), `find_latlng` or `find_loc`, and `page`.
The *original* assumption was that it also required cookies satisfying
Cloudflare bot management -- later disproven, see "Session cookies" below
for the full story; skip ahead there if you're wondering which is actually
true, since this paragraph predates that finding. The response shape is
confirmed too -- `scraping/search.py`,
`parsing/search_parser.py`'s field mapping, `etl/extract.py`'s pagination
loop (which reads the response's own `page`/`pageSize`/`totalPages`/
`totalResults` rather than guessing), and `tests/fixtures/search_listing_sample.json`
(a real, full, untrimmed response, not synthetic) are all built directly
against two independent live captures, not a guess.

**One non-obvious thing worth knowing:** a search result row is per-*listing*
(one physical address), not per-*company*. A business with several branches
shows up as several rows sharing `business_id` + `bbb_office_id` but a
different `bbb_id` per address -- confirmed via a business that appeared 3x
on one page, once per branch (see `BusinessSummary.bbb_id`'s docstring and
`tests/parsing/test_search_parser.py::test_multi_branch_business_keeps_each_listing_distinct`).
Deduping on the wrong key here silently merges distinct branches into one
record -- an early version of this mapping did exactly that before a second
real capture caught it.

Same trap, different field, caught by a second real capture: `reportUrl` is
identical across every branch of a multi-location business (it points at
the canonical address only) -- `localReportUrl` is the one that actually
carries the branch-specific `/addressId/N` suffix. `profile_url` now
prefers it when present; using `reportUrl` alone would silently fetch the
wrong branch's page for every non-canonical address.

`data/reference/categories.json` also holds 10 real `(id, name)` pairs
pulled from that response's category filters -- just that one narrow
finance-related slice though, not the full taxonomy.

**Also confirmed real (2026-09-01):** the individual business-profile page
-- same `window.__PRELOADED_STATE__` mechanism as guessed, confirmed real
this time, with the actual data at `businessProfile` (not `business`).
`parsing/business_parser.py`'s field mapping and
`tests/fixtures/business_page_sample.html` (a real captured page, not
synthetic) are built directly against it. Also confirmed: BBB obfuscates
emails in the page JSON (`"!~xK_bL!user__at__domain__dot__tld!~xK_bL!"`,
decoded client-side by BBB's own frontend before display) -- `email` comes
back already decoded; see `_deobfuscate_email`'s docstring.

**Still placeholder:** `data/reference/categories.json` -- real but narrow
(10 finance/accounting categories, a side effect of which query happened to
get captured first). Needs the rest of BBB's industries. See
`data/reference/README.md` for how to grow it incrementally from every real
search response, or fill in `scripts/fetch_categories.py` for a proper
full-taxonomy source once you find one.

**Resolved (2026-09-02): profile-page fetching was unreliable, now isn't.**
Individual profile pages were getting Cloudflare-challenged even with a
valid, unexpired, completely unmodified captured session -- confirmed by
replaying the exact original captured request script standalone (no proxy,
no code of ours involved) and still getting a 403 "Just a moment..." page,
in the same run where `/api/search` succeeded normally. Root cause: plain
`requests`/urllib3's TLS handshake doesn't match a real browser's, and BBB
fingerprints that more aggressively on profile pages than on search.
`scraping/client.py` now uses `curl_cffi` (browser TLS/HTTP2 impersonation,
same technique as the curl-impersonate project) instead of `requests` --
same cookies, same everything else, immediately fixed. Verified live
end-to-end: a 15-business `--details` run that previously got 15/15 profile
fetches blocked now gets 15/15 through cleanly. Follow-up finding: the
cookies turned out not to be necessary at all -- see "Session cookies"
below for the fuller story and `scraping/session.py`'s / `scraping/client.py`'s
module docstrings for the details.

The generic HTML-extraction helpers (`parsing/json_extract.py` -- pulling
`<script type="application/json">` blocks, or a `window.X = {...}` state
blob with proper brace-matching for nested JSON) are only needed for the
business-profile page now; search doesn't go through HTML at all.

## Session cookies (optional, not required)

`bbb.org` sits behind Cloudflare bot management. The original assumption
was that a captured `cf_clearance` + `CF_Authorization` + site cookies were
what got requests through -- `bbb_scraper/scraping/session.py` loads them
from `data/secrets/bbb_session.json` (gitignored, never committed -- copy
`data/secrets/bbb_session.example.json` and fill in real values) and merges
them into every request, if the file exists.

**That assumption turned out to be wrong, in a good way.** The real story:

1. Every business-profile-page fetch was getting 403'd (Cloudflare's "Just a
   moment..." challenge) while `/api/search` stayed reliable, on the same
   run, same cookies, same proxy -- and replaying the exact captured
   request completely standalone (no proxy) got the same challenge. The
   cause was a TLS-fingerprint mismatch: plain `requests`/urllib3's
   handshake doesn't match a real browser's, regardless of what headers or
   cookies claim. Fixed by switching `HttpClient`'s transport to
   `curl_cffi` -- browser TLS/HTTP2 impersonation, same technique as the
   [curl-impersonate](https://github.com/lwthiker/curl-impersonate)
   project, but a plain Python package (no Docker/container needed).
   Configured via `HTTP_IMPERSONATE` in `.env` (default `chrome150`).
2. That raised the obvious follow-up: if the fingerprint was the real
   problem, were the cookies ever actually necessary? Tested directly --
   the exact same requests (both `/api/search` and a business-profile page
   that had never been fetched before, ruling out a cached response via
   `cf-cache-status: DYNAMIC` on both) succeeded with **zero cookies**,
   `curl_cffi` impersonation alone. Location personalization doesn't depend
   on cookies either -- it comes from the `find_loc`/`find_latlng` request
   params.

So: `data/secrets/bbb_session.json` is now optional. `HttpClient` runs fine
without it (missing file logs an info line, not a warning). It's kept
available since real cookies can't hurt and might matter for something not
yet identified, or if BBB's protection posture tightens later -- but it's
not a prerequisite, and specifically not something that needs periodic
refreshing just to keep scraping working. What's still real if you do rely
on it: `CF_Authorization`'s ~24h JWT expiry, and `cf_clearance` typically
being bound to the IP that earned it. See `scraping/session.py`'s and
`scraping/client.py`'s module docstrings for the full account, including
the honest caveat that this is verified over one session's testing window,
not a permanent guarantee -- if profile pages start getting blocked again,
a fresh captured session is the first thing to try bringing back.

## Adding a new output destination

1. Subclass `Sink` in `pipeline/sinks/your_sink.py`, implement `load()`.
2. Register it in `pipeline/registry.py`'s `build_sink()`.
3. Add its name to `OUTPUT_SINKS` in `.env` (comma-separated, e.g.
   `OUTPUT_SINKS=csv,sql`).

The ETL pipeline (`etl/pipeline.py`) doesn't need to change.

**Nested fields (categories, contacts, socials, reviews_complaints, ...):**
`transform.py` deliberately keeps these as real Python lists/dicts, not
pre-flattened strings (see its module docstring) -- a destination that
handles structure natively (JSONSink) gets it as-is. A destination that
can't (CSVSink, SQLSink, ExcelSink -- flat cells/columns only, and the
Streamlit UI's table/download) JSON-encodes them itself, right before
writing, so a cell holds real parseable JSON (`["Plumbers", "HVAC"]`)
rather than Python's `str()` repr (`"['Plumbers', 'HVAC']"`, which looks
similar but isn't valid JSON). If you add a new flat-shaped destination,
reuse `bbb_scraper/utils/flatten.py`'s `flatten_record()` rather than
reimplementing this.

## Observability

`RunStats` (`utils/stats.py`) counts requests sent/failed/retried, pages
parsed, parse failures, records extracted/deduped/loaded through the course
of a run, and gets logged at the end (`ETL run complete: ...`). Every raw
response is also saved to `data/raw/{search,business}/{date}/...` with an
entry in `data/raw/manifest.jsonl`, so a parse failure can always be
traced back to the exact HTML that caused it.

## Not yet wired up (by design)

- Proxy session rotation on block/challenge (`BlockedError` is raised and
  surfaced, but doesn't yet trigger an automatic new proxy session). Now
  low-priority: TLS fingerprinting was the actual cause of the profile-page
  blocks, cookies turned out to be unnecessary entirely (see "Session
  cookies" above) -- there's no known scenario left where a new proxy
  session would currently help.
- Session-refresh automation for `data/secrets/bbb_session.json` -- moot for
  now given the above, but if BBB's protection posture changes and cookies
  become load-bearing again, refreshing them is still 100% manual.
- Upsert-on-conflict for `SQLSink` (currently append-only).
- **(Fixed)** `CSVSink` used to write its header from whichever batch hit it
  first and silently truncate a later batch's new fields on append -- fixed
  a while back: it now reads the real on-disk header, appends safely when
  the batch's columns are already covered, or rewrites the file with a
  union header when they're not (see `pipeline/sinks/csv_sink.py`).
- No cross-run dedup on `data/processed/businesses.csv` itself -- it's a
  pure append log by design (every run's records land in it, regardless of
  whether the same business was already scraped in an earlier run). A
  handful of duplicate ids from re-running the same test search across
  separate sessions is expected, not corruption; if a genuinely
  deduplicated master view is ever wanted, that's a one-off pass to build
  (`etl/dedupe.py` already has the logic, just needs pointing at the whole
  file), not something this sink should start doing automatically.
- Publishing to the live site (`scripts/deploy_site.py`) is a manual,
  on-demand step -- nothing watches for new data or a `git push` and
  triggers it automatically. Local `site/data/` updates immediately when a
  search publishes; the *live* CloudFront URL only updates when
  `deploy_site.py` is actually run. A GitHub Action that runs it
  automatically on every push is a real option if that's ever wanted, not
  yet built.

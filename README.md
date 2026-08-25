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
    models.py              # Category, Location (+ parse_location)
    categories.py            # CategoryDirectory: load/search data/reference/categories.json
  scraping/
    client.py           # HttpClient: proxy + retry + rate limit + logging
    proxies.py           # IPRoyal proxy URL construction
    capture.py            # save every raw response to data/raw/ + manifest.jsonl
    search.py              # BBB search/listing requests, filtered by Category + Location
    business.py             # BBB business-profile page requests
  parsing/
    json_extract.py       # generic: <script type=application/json>, window.X = {...}
    models.py              # BusinessSummary, BusinessDetail (pydantic)
    search_parser.py        # listing HTML -> list[BusinessSummary]  (BBB-specific mapping)
    business_parser.py       # profile HTML -> BusinessDetail        (BBB-specific mapping)
  etl/
    identifiers.py         # stable internal business id
    transform.py            # pure functions: model -> flat dict
    dedupe.py                 # dedupe by id
    extract.py                 # Extractor: scraping + parsing combined
    pipeline.py                 # ETLPipeline: orchestrates the whole run
  pipeline/
    base.py                # Sink ABC
    registry.py              # OUTPUT_SINKS env var -> list[Sink]
    sinks/                     # csv, excel, sql, json, http, null
  utils/
    rate_limit.py, hashing.py, stats.py

data/
  reference/categories.json  # BBB industry/category taxonomy (placeholder starter list)

tests/
  fixtures/                 # saved HTML used by parser unit tests
  parsing/                    # parser tests (no network)
  etl/                          # transform/dedupe tests (no network)
  reference/                     # Category/Location tests
  scraping/                        # search URL-building tests

scripts/
  run_search.py             # CLI: pick category + location -> ETL -> configured sinks
  run_business.py             # CLI: fetch + parse one profile page
  fetch_categories.py           # CLI stub: scrape BBB's category taxonomy into data/reference/
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

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev,excel,sql]"
copy .env.example .env
```

Fill in `.env` with your IPRoyal credentials (`IPROYAL_HOST`, `IPROYAL_PORT`,
`IPROYAL_USERNAME`, `IPROYAL_PASSWORD`). Nothing secret lives in the repo --
`config.py` reads everything from environment variables / `.env`, and `.env`
is gitignored.

## Running tests

```bash
pytest
```

Parser tests run entirely against the fixtures in `tests/fixtures/` -- no
network access, no proxies needed. This is intentional: parser development
should never require hitting BBB.

## What's a placeholder vs. what's real

BBB's actual JSON/state schema hasn't been inspected yet, so three things
are explicitly marked `TODO(you)` and built against a synthetic fixture
rather than real captured HTML:

1. **`scraping/search.py` `build_search_url`** -- best-guess query params
   (`find_category`, `find_text`, `find_loc`).
2. **`parsing/search_parser.py`** -- assumes listing JSON looks like
   `{"results": [...]}`. Adjust `_iter_listing_items` / `_map_listing_item`.
3. **`parsing/business_parser.py`** -- assumes a `window.__PRELOADED_STATE__`
   assignment containing `{"business": {...}}`. Adjust `PRELOADED_STATE_VAR`
   / `_map_business_state`.
4. **`data/reference/categories.json`** -- a 10-entry placeholder taxonomy
   with made-up-but-plausible ids/slugs. Replace via `scripts/fetch_categories.py`
   once you know where BBB exposes the full category list (see
   `data/reference/README.md`), and confirm the `id` values match what BBB's
   search actually expects in its category filter.

Workflow for nailing these down:

1. Capture one real search-results page and one real business-profile page
   (`curl` or a browser save, or just let `scraping/capture.py` write them to
   `data/raw/` on a live run).
2. Replace `tests/fixtures/search_listing_sample.html` and
   `tests/fixtures/business_page_sample.html` with the real captures.
3. Update the two parser modules above to match the real shape.
4. `pytest` will fail until the mapping is right -- that's the feedback loop.

The generic extraction helpers (`parsing/json_extract.py` -- pulling
`<script type="application/json">` blocks, or a `window.X = {...}` state
blob with proper brace-matching for nested JSON) don't need to change; they
already work off the real markup regardless of what's inside.

## Adding a new output destination

1. Subclass `Sink` in `pipeline/sinks/your_sink.py`, implement `load()`.
2. Register it in `pipeline/registry.py`'s `build_sink()`.
3. Add its name to `OUTPUT_SINKS` in `.env` (comma-separated, e.g.
   `OUTPUT_SINKS=csv,sql`).

The ETL pipeline (`etl/pipeline.py`) doesn't need to change.

## Observability

`RunStats` (`utils/stats.py`) counts requests sent/failed/retried, pages
parsed, parse failures, records extracted/deduped/loaded through the course
of a run, and gets logged at the end (`ETL run complete: ...`). Every raw
response is also saved to `data/raw/{search,business}/{date}/...` with an
entry in `data/raw/manifest.jsonl`, so a parse failure can always be
traced back to the exact HTML that caused it.

## Not yet wired up (by design)

- Proxy session rotation on block/challenge (`BlockedError` is raised and
  surfaced, but doesn't yet trigger an automatic new IPRoyal session --
  add that in `etl/extract.py` once you see how BBB actually responds to
  blocks).
- Real BBB search URL params / listing+state JSON schema (see above).
- Upsert-on-conflict for `SQLSink` (currently append-only).

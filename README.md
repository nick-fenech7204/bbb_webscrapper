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
    proxies.py           # provider-agnostic proxy URL construction (Decodo, IPRoyal, ...)
    session.py            # loads BBB session cookies/headers from data/secrets/
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
  secrets/                     # gitignored -- bbb_session.json (real cookies/headers)

tests/
  fixtures/                 # saved HTML/JSON used by parser unit tests
  parsing/                    # parser tests (no network)
  etl/                          # transform/dedupe tests (no network)
  reference/                     # Category/Location tests
  scraping/                        # search param-building + proxy tests

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
It requires cookies that satisfy Cloudflare bot management (see "Session
cookies" below). The response shape is confirmed too -- `scraping/search.py`,
`parsing/search_parser.py`'s field mapping, `etl/extract.py`'s pagination
loop (which reads the response's own `page`/`pageSize`/`totalPages`/
`totalResults` rather than guessing), and `tests/fixtures/search_listing_sample.json`
(a real trimmed response, not synthetic) are all built directly against a
live capture, not a guess. `data/reference/categories.json` also holds 10
real `(id, name)` pairs pulled from that response's category filters, though
just that one narrow finance-related slice, not the full taxonomy.

**Still placeholder:**

1. **`parsing/business_parser.py`** -- assumes a `window.__PRELOADED_STATE__`
   assignment containing `{"business": {...}}` on the individual profile
   page (still HTML -- only search turned out to be a JSON API). Adjust
   `PRELOADED_STATE_VAR` / `_map_business_state` once you've captured one.
2. **`data/reference/categories.json`** -- real but narrow (10
   finance/accounting categories, a side effect of which query happened to
   get captured first). Needs the rest of BBB's industries. See
   `data/reference/README.md` for how to grow it incrementally from every
   real search response, or fill in `scripts/fetch_categories.py` for a
   proper full-taxonomy source once you find one.

Workflow for the business-profile page (same pattern that got search done):

1. Capture one real business-profile HTML page (`scraping/capture.py`
   writes every response to `data/raw/` automatically on a live run).
2. Replace `tests/fixtures/business_page_sample.html` with the real capture.
3. Update `business_parser.py` to match the real shape.
4. `pytest` will fail until the mapping is right -- that's the feedback loop.

The generic HTML-extraction helpers (`parsing/json_extract.py` -- pulling
`<script type="application/json">` blocks, or a `window.X = {...}` state
blob with proper brace-matching for nested JSON) are only needed for the
business-profile page now; search doesn't go through HTML at all.

## Session cookies

`bbb.org` sits behind Cloudflare bot management -- plain requests get
challenged. `bbb_scraper/scraping/session.py` loads cookies/headers captured
from a real browser session out of `data/secrets/bbb_session.json`
(gitignored, never committed -- copy `data/secrets/bbb_session.example.json`
and fill in real values) and merges them into every request.

This is fragile by nature, not a bug to fix once: the captured
`CF_Authorization` is a JWT with a real expiry (~24h in the session this was
built against), and `cf_clearance` is typically bound to the IP that earned
it -- so a session captured from your own browser may simply not validate
once routed through a proxy IP. There's no code fix for that here; it's a
real constraint for whatever comes next (refreshing per proxy session,
solving the challenge through the proxy itself, etc.). If cookie/header
replay alone stops being enough even from a matching IP, plain
`requests`/urllib3's TLS handshake fingerprint not matching real Chrome is
the next thing to suspect.

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
  surfaced, but doesn't yet trigger an automatic new proxy session -- add
  that in `etl/extract.py` once you see how BBB actually responds to
  blocks).
- Real BBB search URL params / listing+state JSON schema (see above).
- Upsert-on-conflict for `SQLSink` (currently append-only).

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
    client.py           # HttpClient: curl_cffi (browser TLS impersonation) + proxy + retry + rate limit
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
fetches blocked now gets 15/15 through cleanly. See `scraping/session.py`'s
docstring and `scraping/client.py`'s module docstring for the full story.

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
real, ongoing constraint -- refresh `data/secrets/bbb_session.json` from a
new browser session when requests start getting challenged again (there's
no automation for this yet, see "Not yet wired up" below).

**Resolved 2026-09-02: profile pages needed more than cookies could give
them.** `/api/search` was reliable while every business-profile-page fetch
got 403'd on the same run, same cookies, same proxy -- even replaying the
exact captured request completely standalone (no proxy) got the same
challenge. Turned out to be the TLS-fingerprint issue mentioned above, just
worse on profile pages than on search: plain `requests`/urllib3's TLS
handshake doesn't match a real browser's, regardless of headers or cookies.
Fixed by switching `HttpClient`'s transport to `curl_cffi` -- browser
TLS/HTTP2 impersonation, same technique as the
[curl-impersonate](https://github.com/lwthiker/curl-impersonate) project,
but installable as a plain Python package (no Docker/container needed).
Configured via `HTTP_IMPERSONATE` in `.env` (default `chrome150`). Verified
live: the exact same captured session that got 15/15 profile fetches
blocked before got 15/15 through cleanly after, no other changes. See
`scraping/client.py`'s module docstring.

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
  surfaced, but doesn't yet trigger an automatic new proxy session). Less
  urgent now that TLS fingerprinting (the actual cause of the profile-page
  blocks) is fixed -- `cf_clearance`'s IP-binding is still a real,
  independent constraint worth building this for eventually.
- Any kind of session-refresh automation (see "Session cookies" above) --
  currently 100% manual (re-capture from a browser, overwrite
  data/secrets/bbb_session.json). `curl_cffi` fixed the fingerprint problem,
  not the "cookies eventually expire" one.
- Upsert-on-conflict for `SQLSink` (currently append-only).
- `CSVSink` writes its header from whichever batch of records hits it
  first; a later batch with different/more fields (e.g. summaries then
  details, or a schema change) gets silently truncated to that original
  header on append rather than growing to fit. Fine for a single run, worth
  fixing before relying on it across many runs with evolving fields.

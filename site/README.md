# Static site

Genuinely static -- plain HTML/CSS/JS, no build step, no backend. Reads
pre-published data files in `data/` and does all filtering/sorting/CSV
export in the browser. This is deliberate: see the main
[README's "Static site" section](../README.md) for why (in short: a public
"run a scrape for me" button on your proxy account is a cost/abuse risk;
this only ever reads data you've already collected and chosen to publish).

## Structure

```
site/
  index.html       page markup
  css/style.css     styling (light/dark aware)
  js/app.js         all interactivity -- loads data/manifest.json, then
                     the selected dataset's JSON, filters/sorts/exports
                     client-side
  data/
    manifest.json    which datasets exist (metro, industry, filename, count)
    <id>.json        one file per industry+metro dataset
```

## Publishing a new dataset

Three ways data actually gets here, all funneling through the same
underlying logic in `scripts/publish_site_data.py`:

1. **From a CSV on disk** -- after a pipeline run produces one (e.g. from
   `scripts/run_search.py --metro ...`), publish it by hand:
   ```bash
   python scripts/publish_site_data.py data/processed/miami_car_dealers_full.csv \
       --industry "Car Dealers" --metro "Miami, FL"
   ```
   (`publish_dataset()` -- reads the CSV, JSON-decodes the flattened
   `categories`/`contacts`/`socials`/`reviews_complaints` columns back into
   real lists/dicts.)
2. **Straight from a running search, no CSV round-trip** -- the main
   Streamlit page's "Also publish to the static site" checkbox (on by
   default) calls `publish_records()` directly on the just-scraped,
   already-in-memory records, one dataset per place searched. Bonus of
   skipping the CSV round-trip: real types are preserved (`accredited:
   true`, not the string `"True"`) -- `js/app.js`'s `isTrue()` handles both
   shapes either way, so this isn't something you need to worry about.
3. **Automatically, per metro, from a batch run** -- `scripts/
   batch_scrape_metros.py` (and the Batch Scraper Streamlit page) calls
   `publish_dataset()` on each metro's checkpoint CSV the moment it
   finishes, unless run with `--no-publish`.

All three write/overwrite `site/data/<industry>--<metro>.json` and update
`site/data/manifest.json` (only that one entry -- other published datasets
are untouched). Re-run any of them any time to refresh a dataset with newer
data.

Field selection (what's public vs. dropped) is `_PUBLIC_FIELDS` in
`publish_site_data.py` -- edit it there to add/remove a column everywhere
at once (site table, CSV export, and the underlying JSON), regardless of
which of the three paths above produced the data.

**Publishing here only updates local files** -- see "Deploying" below to
actually push a change onto the live site.

## Previewing locally

```bash
python -m http.server 8502 --directory site
```

Then open http://localhost:8502. (Needs a real HTTP server, not a
`file://` URL -- browsers block `fetch()` of local JSON files over
`file://`.) Also available via `.claude/launch.json`'s `"site"` config if
you're driving this through Claude Code's browser preview.

## Deploying

See [DEPLOY.md](DEPLOY.md) -- a step-by-step AWS console walkthrough
(S3 + CloudFront), written to explain the pieces, not just click through.

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

After a pipeline run produces a CSV (e.g. from `scripts/run_search.py
--metro ...`), publish it:

```bash
python scripts/publish_site_data.py data/processed/miami_car_dealers_full.csv \
    --industry "Car Dealers" --metro "Miami, FL"
```

This writes/overwrites `site/data/<industry>--<metro>.json` and updates
`site/data/manifest.json` (only that one entry -- other published datasets
are untouched). Re-run it any time to refresh a dataset with newer data.

Field selection (what's public vs. dropped) is `_PUBLIC_FIELDS` in that
script -- edit it there to add/remove a column everywhere at once (site
table, CSV export, and the underlying JSON).

## Previewing locally

```bash
python -m http.server 8502 --directory site
```

Then open http://localhost:8502. (Needs a real HTTP server, not a
`file://` URL -- browsers block `fetch()` of local JSON files over
`file://`.) Also available via `.claude/launch.json`'s `"site"` config if
you're driving this through Claude Code's browser preview.

## Deploying

Not yet set up -- see the main README for the plan (S3 + CloudFront).

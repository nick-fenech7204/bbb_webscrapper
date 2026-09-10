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
  index.html         "Lead records" page -- the standard BBB lead
  intelligence.html   "Intelligence" page -- BBB matched to Yelp, scored
  css/style.css        styling (light/dark aware)
  js/app.js            all interactivity, shared by both pages -- picks a
                        column set + default sort from <body data-view>,
                        loads data/manifest.json then the selected
                        dataset's JSON, filters/sorts/exports client-side
  data/
    manifest.json      which datasets exist (metro, industry, filename,
                        record_count, has_intel)
    <id>.json          one file per industry+metro dataset, read by BOTH pages
```

Each record in a dataset JSON carries: the BBB public fields, a
`last_updated` date, the matched Yelp fields (`yelp_name` / `yelp_rating` /
`yelp_review_count` / `yelp_url` -- null when unmatched), and our derived
columns (`review_need_score`, `lead_priority_score`, the flags). The Lead
records page shows the BBB columns; the Intelligence page shows the Yelp +
derived columns. Raw Yelp fields beyond those four (phone, id, price, ...)
stay local -- see `_YELP_SITE_FIELDS` / `_INTEL_SITE_FIELDS` in
`publish_site_data.py`.

## Publishing a new dataset

Ways data gets here, all funneling through `scripts/publish_site_data.py`:

1. **Automatically, per metro, from a batch run** (the normal path) --
   `scripts/batch_scrape_metros.py` calls `publish_master_rows()` on each
   metro's master table the moment it finishes, unless `--no-publish`. This
   carries the Yelp + intelligence columns. `yelp_only` rows are dropped.
2. **From a master-table CSV on disk** -- e.g. `scripts/match_bbb_yelp.py`
   output:
   ```bash
   python scripts/publish_site_data.py --master \
       data/processed/bbb_yelp_master__car-dealers-miami-fl.csv \
       --industry "Car Dealers" --metro "Miami, FL"
   ```
3. **From a BBB-only CSV on disk** -- `publish_dataset()`; the record gets
   `last_updated` and null Yelp/intelligence fields (the Intelligence page
   shows a "no Yelp enrichment" note for that dataset).
4. **In-memory BBB records** -- `publish_records()`, same as (3) but no CSV.

All write/overwrite `site/data/<industry>--<metro>.json` and update the one
matching `manifest.json` entry (others untouched). Re-run any time to
refresh.

Field selection: `_PUBLIC_FIELDS` (BBB), `_YELP_SITE_FIELDS`,
`_INTEL_SITE_FIELDS` in `publish_site_data.py` -- edit there to change a
column everywhere at once.

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

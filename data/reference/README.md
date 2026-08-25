# categories.json

This is BBB's industry/category taxonomy, used to drive category-based
search (`Category.id` gets passed straight into the search request -- see
`bbb_scraper/scraping/search.py`).

**`categories.json` currently holds a small placeholder starter list** (10
common categories with made-up-but-plausible ids/slugs), just so the
framework has something to load and the tests/CLI have something to exercise.

## Replacing it with the real list

BBB publishes/exposes its full category list somewhere (a directory page, or
embedded JSON on the search page itself, similar to listing results). Once
you've found it:

1. Fill in `scripts/fetch_categories.py`'s `_parse_categories_page` (or write
   your own extraction if the source is different, e.g. a static directory
   page vs. an embedded JSON blob).
2. Run it:
   ```bash
   python scripts/fetch_categories.py
   ```
   which overwrites `data/reference/categories.json`.
3. Confirm `id` here matches whatever value BBB's search actually expects in
   its category filter param -- check a real search request (curl/browser
   devtools) for a known category and compare.

## Schema

```json
{ "id": "plumbers", "name": "Plumbers", "slug": "plumbers" }
```

- `id` -- required. Whatever value goes into the search request's category
  filter.
- `name` -- required. Human-readable, used for interactive selection/search.
- `slug` -- optional. Only needed if BBB's URLs use a separate human-readable
  slug distinct from `id`.

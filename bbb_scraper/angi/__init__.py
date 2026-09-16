"""Angi (angi.com) scraping -- companylist category+metro listings, and the
individual business profile ("company") pages they link to.

Layered the same way as the rest of this project (scraping -> parsing ->
orchestration), but Angi's own shape is different enough from BBB's to be
its own package rather than force-fit into bbb_scraper/scraping|parsing:

  - flight_data.py -- reassembles Angi's Next.js "flight" payload (many
    `self.__next_f.push([1,"..."])` chunks, each a JSON-escaped string) into
    one plain-text blob. Confirmed necessary, not optional: the escaping
    depth varies between pages (single vs. double backslash-escaped
    depending on the page), so hunting the raw, still-escaped HTML with a
    regex is fragile -- reassembling first means every downstream regex only
    ever deals with one clean, consistent representation.
  - models.py / parsing.py -- pure functions, blob text in, dataclasses out.
    No network calls, so these are the easy, fast part to unit test (see
    tests/angi/ -- fixtures are real captured pages, not hand-written HTML).
  - client.py -- the network part: proxied by default (2026-09-15, after a
    real production 429 -- see its own module docstring), a fresh, bare
    (never sticky) connection per request. Still paced on top of that --
    repeated requests to *one* host (angi.com itself), unlike webcheck's
    one-request-per-different-host pattern, so it gets BBB-style
    politeness pacing rather than webcheck's higher, many-different-hosts
    concurrency.
  - scraper.py -- orchestration: paginate a (category, metro) listing, then
    fetch+parse each business's own profile page.
"""

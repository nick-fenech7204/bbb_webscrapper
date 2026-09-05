"""
BBB Scraper -- control panel UI.

Run locally:
    streamlit run streamlit_app.py

Deploy: push this repo to GitHub (already done), connect it at
share.streamlit.io, point it at streamlit_app.py -- free hosting, and the
default filename is what Streamlit Community Cloud looks for automatically.

Read this before deploying anywhere reachable by anyone but you: this page
*is* the live backend -- clicking "Run search" makes real requests through
your real proxy using your real BBB session. It is not the same thing as
the "cheap static public insight site" discussed separately (that one reads
pre-generated data files, no live scraping involved). Put this behind auth
before it's public, or keep it private -- there's none built in here.

This file is a thin presentation layer, nothing more: it calls the exact
same Extractor / transform / dedupe / sinks everything else in the repo
uses. No scraping or parsing logic lives here -- if a search or a field
mapping needs to change, that's still bbb_scraper/, not this file.

Revamped 2026-09-04 (Nick's request) to a single, simplified search flow:
one form, no mode picker, no exposed radius/population/pages knobs. Every
place typed in gets `Extractor.extract_search_area_coverage` -- the same
smart "sweep real nearby cities" logic the batch scraper's metro mode uses,
generalized to work for ANY resolvable place, not just the curated
data/reference/metros.json list (see that method's docstring). A big metro
sweeps wide automatically; a small town with nothing substantial nearby
just searches itself -- no separate "which mode do I want" decision needed.
The old lat/lon ring sweep (`extract_search_coverage`) and the
curated-metro-only mode are still in bbb_scraper/ (still used by the CLI
and the Batch Scraper page, where a fixed named-metro list is genuinely the
right tool), just not exposed on this page anymore.
"""
from __future__ import annotations

import re

import pandas as pd
import streamlit as st

from bbb_scraper.config import settings
from bbb_scraper.etl.dedupe import dedupe_records
from bbb_scraper.etl.extract import Extractor
from bbb_scraper.etl.transform import transform_detail, transform_summary
from bbb_scraper.logging_setup import configure_logging
from bbb_scraper.pipeline.registry import build_sinks_from_settings
from bbb_scraper.reference.models import Category, parse_location
from bbb_scraper.utils.flatten import flatten_record
from bbb_scraper.utils.stats import RunStats

configure_logging()
st.set_page_config(page_title="BBB Scraper", page_icon="\U0001F4CB", layout="wide")

# Fixed sweep parameters -- proven values from real runs (Miami: tight,
# dense local cluster; Providence: a much larger multi-state sweep, 10x
# Miami's count at these same settings), no longer exposed as UI knobs on
# this page per Nick's request (2026-09-04): one less decision per search.
# Still overridable for power users via the CLI (scripts/run_search.py
# --metro-radius etc.) or the batch scraper -- this page just always uses
# what's worked well so far.
SWEEP_RADIUS_MILES = 40.0
SWEEP_MIN_POPULATION = 25_000
SWEEP_MAX_PAGES_PER_PLACE = 15

# No dropdown/directory lookup for the industry field on purpose: BBB's
# search takes the industry phrase directly as `find_text` (confirmed
# 2026-09-01 -- see reference/models.py's Category docstring) and accepts a
# wide range of phrasing, so data/reference/categories.json's curated
# 11-entry list isn't a gate on what you can search -- it's still used by
# the CLI's --category resolution and by scripts/fetch_categories.py, just
# not by this form.


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-") or "custom"


def _build_category(text: str) -> Category | None:
    text = text.strip()
    if not text:
        return None
    return Category(id=_slugify(text), name=text)


st.title("BBB Scraper")
st.caption(
    "Type an industry and one or more places. Each place is searched together "
    "with real nearby towns automatically -- a big metro sweeps wide, a small "
    "town with nothing substantial nearby just searches itself."
)

with st.sidebar:
    st.header("Search")
    with st.form("search_form"):
        industry_text = st.text_input(
            "Industry / category",
            placeholder="e.g. Roofing Contractors, Plumbers, Heating and Air Conditioning, CPA",
        )
        st.caption("Sent as-is to BBB's search -- most industry phrasing works.")

        locations_text = st.text_area(
            "Places, one per line",
            placeholder="Miami, FL\nPalm Coast, FL\n78701",
            height=100,
            help='Any city/state, ZIP, or town BBB can resolve -- a major metro or '
                 'somewhere small alike. Not limited to a fixed list.',
        )
        st.caption(
            f"Each place is swept together with real nearby towns within "
            f"{SWEEP_RADIUS_MILES:g} miles (population {SWEEP_MIN_POPULATION:,}+) -- "
            "automatic, nothing to configure. Nothing substantial nearby just means "
            "that place gets searched on its own."
        )

        fetch_details = st.checkbox(
            "Fetch full details (contacts, socials, reviews)",
            value=False,
            help="One extra request per business -- can take a while for a large "
                 "result set. Off fetches listing data only.",
        )
        save_to_sinks = st.checkbox(
            f"Also save to configured sinks ({', '.join(settings.output_sink_names)})",
            value=True,
        )
        submitted = st.form_submit_button("Run search", type="primary", use_container_width=True)

    with st.expander("Configuration"):
        st.write(f"**Proxy:** {'enabled' if settings.proxy_enabled else 'disabled'}"
                  + (f" ({settings.proxy_host})" if settings.proxy_enabled else ""))
        st.write(f"**Impersonation:** `{settings.http_impersonate}`")
        st.write(f"**BBB session file:** "
                  f"{'found' if settings.bbb_session_file.exists() else 'not found (optional)'}")
        st.write(
            f"**Sweep settings:** {SWEEP_RADIUS_MILES:g}mi radius, "
            f"{SWEEP_MIN_POPULATION:,}+ population, {SWEEP_MAX_PAGES_PER_PLACE} pages/place "
            "-- fixed, see scripts/run_search.py for CLI overrides."
        )

if submitted:
    category = _build_category(industry_text)
    locations = [parse_location(line.strip()) for line in locations_text.splitlines() if line.strip()]

    if category is None:
        st.error("Enter a category phrase.")
        st.stop()
    if not locations:
        st.error("Enter at least one place.")
        st.stop()

    stats = RunStats()
    all_records = []
    progress = st.progress(0.0)
    status = st.empty()

    with Extractor(stats=stats) as extractor:
        for i, location in enumerate(locations):

            def _on_place_done(place_name, done, total, running_count, _i=i, _n=len(locations), _loc=location):
                overall = (_i + done / total) / _n if total else (_i + 1) / _n
                progress.progress(min(overall, 1.0))
                status.write(
                    f"**{_loc.display}** ({_i + 1}/{_n}): searched **{place_name}** "
                    f"({done}/{total} place{'s' if total != 1 else ''}) -- "
                    f"{running_count} businesses found so far…"
                )

            summaries = extractor.extract_search_area_coverage(
                category, location,
                radius_miles=SWEEP_RADIUS_MILES, min_population=SWEEP_MIN_POPULATION,
                max_pages_per_place=SWEEP_MAX_PAGES_PER_PLACE,
                on_place_done=_on_place_done,
            )
            all_records.extend(transform_summary(s) for s in summaries)

            if fetch_details:
                for j, summary in enumerate(summaries):
                    if not summary.profile_url:
                        continue
                    status.write(
                        f"Fetching details for **{location.display}**: "
                        f"{j + 1}/{len(summaries)}…"
                    )
                    try:
                        detail = extractor.extract_business(summary.profile_url)
                        all_records.append(transform_detail(detail))
                    except Exception as exc:
                        st.warning(f"Failed to fetch detail for {summary.profile_url}: {exc}")

    all_records = dedupe_records(all_records, stats=stats)
    status.empty()
    progress.empty()

    if save_to_sinks:
        for sink in build_sinks_from_settings():
            try:
                sink.load(all_records)
            except Exception as exc:
                st.warning(f"Sink {sink.name!r} failed: {exc}")

    st.session_state["results"] = all_records
    st.session_state["stats"] = stats.as_dict()
    st.success(f"Done — {len(all_records)} unique record(s). {stats.summary_line()}")

if "results" in st.session_state and st.session_state["results"]:
    records = st.session_state["results"]
    df = pd.DataFrame([flatten_record(r) for r in records])

    st.subheader(f"Results ({len(df)})")
    st.dataframe(df, use_container_width=True, height=500)

    st.download_button(
        "Download CSV",
        df.to_csv(index=False).encode("utf-8"),
        file_name="bbb_results.csv",
        mime="text/csv",
    )
elif not submitted:
    st.info("Fill in the search form in the sidebar and click **Run search** to get started.")

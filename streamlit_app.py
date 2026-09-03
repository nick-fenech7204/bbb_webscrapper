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
from bbb_scraper.reference.metros import MetroDirectory
from bbb_scraper.reference.models import Category, parse_location
from bbb_scraper.utils.flatten import flatten_record
from bbb_scraper.utils.stats import RunStats

configure_logging()
st.set_page_config(page_title="BBB Scraper", page_icon="\U0001F4CB", layout="wide")

# No dropdown/directory lookup here on purpose: BBB's search takes the
# industry phrase directly as `find_text` (confirmed 2026-09-01 -- see
# reference/models.py's Category docstring) and accepts a wide range of
# phrasing, so data/reference/categories.json's curated 11-entry list isn't
# a gate on what you can search -- it's still used by the CLI's --category
# resolution and by scripts/fetch_categories.py, just not by this form.


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-") or "custom"


def _build_category(text: str) -> Category | None:
    text = text.strip()
    if not text:
        return None
    return Category(id=_slugify(text), name=text)


st.title("BBB Scraper")
st.caption(
    "Search + optionally enrich BBB business listings by category and location. "
    "Wraps the same Extractor/transform/dedupe/sinks the CLI scripts use."
)

with st.sidebar:
    st.header("Search")
    with st.form("search_form"):
        industry_text = st.text_input(
            "Industry / category",
            placeholder="e.g. Roofing Contractors, Plumbers, Heating and Air Conditioning, CPA",
        )
        st.caption("Sent as-is to BBB's search -- most industry phrasing works.")

        search_mode = st.radio(
            "Search mode",
            [
                "Single location(s)",
                "Coverage sweep (points around a location)",
                "Metro sweep (real nearby cities)",
            ],
            help="Coverage sweep scatters lat/lon points around each location below. "
                 "Metro sweep instead searches real, substantial nearby cities/CDPs by "
                 "name -- confirmed 2026-09-02 this reaches real local results a lat/lon "
                 "sweep can't (BBB's \"local\" pool is tied to the specific named place "
                 "searched, not just proximity to a point). See README.",
        )
        # Every field below is always rendered and enabled regardless of search_mode
        # -- st.form only re-evaluates on submit, so anything gated on the radio's
        # live value would render using its state from *before* the click that
        # changed it, one submit behind. Same fix already applied to the free-text
        # category field and the old coverage checkbox. Unused fields for whichever
        # mode isn't selected are simply ignored below.
        locations_text = st.text_area(
            "Locations, one per line",
            placeholder="Seattle, WA\nTacoma, WA\nBellevue, WA",
            height=100,
            help="Used by \"Single location(s)\" and \"Coverage sweep\" -- ignored "
                 "for \"Metro sweep\" (pick a metro below instead).",
        )
        max_pages = st.number_input(
            "Max pages per location", min_value=1, max_value=settings.bbb_max_search_pages,
            value=settings.bbb_max_search_pages,
            help="BBB caps results at 15 pages regardless of how high this is set. "
                 "\"Single location(s)\" mode only -- coverage/metro sweeps use their "
                 "own pages-per-place settings below.",
        )

        st.caption("Coverage sweep settings (used only in that mode):")
        cov_col1, cov_col2, cov_col3 = st.columns(3)
        radius_miles = cov_col1.number_input("Radius (mi)", min_value=1.0, value=25.0, step=5.0)
        num_points = cov_col2.number_input(
            "Search points", min_value=2, max_value=64, value=16, step=2,
        )
        max_pages_per_point = cov_col3.number_input(
            "Pages/point", min_value=1, max_value=settings.bbb_max_search_pages, value=2,
        )
        st.caption(
            "Coverage sweep multiplies request count roughly by points × pages/point, "
            "per location line -- keep both modest rather than maxing them out."
        )

        st.caption("Metro sweep settings (used only in that mode):")
        metro_directory = MetroDirectory.load()
        metro_options = {m.id: m.name for m in metro_directory.all()}
        selected_metro_id = (
            st.selectbox(
                "Metro", options=list(metro_options.keys()),
                format_func=lambda mid: metro_options[mid],
            )
            if metro_options
            else None
        )
        if not metro_options:
            st.caption(
                "No metros available -- data/reference/metros.json missing or empty."
            )
        metro_col1, metro_col2, metro_col3 = st.columns(3)
        metro_radius = metro_col1.number_input(
            "Radius (mi)", min_value=1.0, value=40.0, step=5.0, key="metro_radius"
        )
        metro_min_population = metro_col2.number_input(
            "Min population", min_value=0, value=25_000, step=5_000,
        )
        metro_max_pages = metro_col3.number_input(
            "Pages/place", min_value=1, max_value=settings.bbb_max_search_pages, value=15,
            key="metro_max_pages",
        )
        st.caption(
            "Metro sweep searches every real city/CDP at or above the population "
            "floor within the radius -- a big metro at a low floor can mean dozens "
            "of places × pages/place in requests. Keep the floor reasonably high "
            "(25,000+) for a first run."
        )

        fetch_details = st.checkbox(
            "Fetch full details (contacts, socials, reviews)",
            value=False,
            help="One extra request per business -- can take several minutes for a "
                 "large result set. Off fetches listing data only.",
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

if submitted:
    category = _build_category(industry_text)
    if category is None:
        st.error("Enter a category phrase.")
        st.stop()

    is_metro_mode = search_mode.startswith("Metro")
    is_coverage_mode = search_mode.startswith("Coverage")

    metro = None
    locations = []
    if is_metro_mode:
        metro = metro_directory.get(selected_metro_id) if selected_metro_id else None
        if metro is None:
            st.error("Select a metro (or run scripts/build_us_cities.py if none are listed).")
            st.stop()
    else:
        locations = [parse_location(line.strip()) for line in locations_text.splitlines() if line.strip()]
        if not locations:
            st.error("Enter at least one location.")
            st.stop()

    stats = RunStats()
    all_records = []
    progress = st.progress(0.0)
    status = st.empty()

    def _fetch_details(extractor: Extractor, summaries, label: str) -> list[dict]:
        records = []
        for j, summary in enumerate(summaries):
            if not summary.profile_url:
                continue
            status.write(f"Fetching details for **{label}**: {j + 1}/{len(summaries)}…")
            try:
                detail = extractor.extract_business(summary.profile_url)
                records.append(transform_detail(detail))
            except Exception as exc:
                st.warning(f"Failed to fetch detail for {summary.profile_url}: {exc}")
        return records

    with Extractor(stats=stats) as extractor:
        if is_metro_mode:
            status.write(f"Sweeping **{category.name}** across the **{metro.name}** metro area…")
            summaries = extractor.extract_search_metro_coverage(
                category, metro, radius_miles=metro_radius,
                min_population=int(metro_min_population), max_pages_per_place=int(metro_max_pages),
            )
            all_records.extend(transform_summary(s) for s in summaries)
            if fetch_details:
                all_records.extend(_fetch_details(extractor, summaries, metro.name))
            progress.progress(1.0)
        else:
            for i, location in enumerate(locations):
                if is_coverage_mode:
                    status.write(
                        f"Sweeping **{category.name}** around **{location.display}** "
                        f"({int(num_points)} points, {radius_miles:g}mi)…"
                    )
                    summaries = extractor.extract_search_coverage(
                        category, location, radius_miles=radius_miles,
                        num_points=int(num_points), max_pages_per_point=int(max_pages_per_point),
                    )
                else:
                    status.write(f"Searching **{category.name}** in **{location.display}**…")
                    summaries = extractor.extract_search(category, location, max_pages=int(max_pages))
                all_records.extend(transform_summary(s) for s in summaries)

                if fetch_details:
                    all_records.extend(_fetch_details(extractor, summaries, location.display))

                progress.progress((i + 1) / len(locations))

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

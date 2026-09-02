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

        locations_text = st.text_area(
            "Locations, one per line",
            placeholder="Seattle, WA\nTacoma, WA\nBellevue, WA",
            height=100,
        )
        max_pages = st.number_input(
            "Max pages per location", min_value=1, max_value=settings.bbb_max_search_pages,
            value=settings.bbb_max_search_pages,
            help="BBB caps results at 15 pages regardless of how high this is set. "
                 "Ignored when coverage mode (below) is on.",
        )

        coverage_mode = st.checkbox(
            "Coverage mode -- sweep multiple points instead of one search per location",
            value=False,
            help="BBB's location search doesn't actually scope to a local radius -- "
                 "confirmed empirically, see README. This works around it by searching "
                 "several points around each location instead of just one.",
        )
        # Always rendered and always enabled (not gated on coverage_mode) on purpose:
        # st.form only re-evaluates on submit, so anything conditioned on the checkbox
        # -- visibility, disabled=, a caption -- would render using its state *before*
        # the click that changed it, one submit behind. Same lag the category field
        # used to have before it was made always-rendered too; the fix here is the
        # same one. These three are simply unused when coverage_mode is off.
        st.caption("Coverage settings (used only when coverage mode above is checked):")
        cov_col1, cov_col2, cov_col3 = st.columns(3)
        radius_miles = cov_col1.number_input("Radius (mi)", min_value=1.0, value=25.0, step=5.0)
        num_points = cov_col2.number_input(
            "Search points", min_value=2, max_value=64, value=16, step=2,
        )
        max_pages_per_point = cov_col3.number_input(
            "Pages/point", min_value=1, max_value=settings.bbb_max_search_pages, value=2,
        )
        st.caption(
            "Coverage mode multiplies request count roughly by points × pages/point, "
            "per location line -- keep both modest rather than maxing them out."
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
    locations = [parse_location(line.strip()) for line in locations_text.splitlines() if line.strip()]

    if category is None:
        st.error("Enter a category phrase.")
        st.stop()
    if not locations:
        st.error("Enter at least one location.")
        st.stop()

    stats = RunStats()
    all_records = []
    progress = st.progress(0.0)
    status = st.empty()

    with Extractor(stats=stats) as extractor:
        for i, location in enumerate(locations):
            if coverage_mode:
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

"""
Batch scraper -- run one industry across many metros in one sitting.

Thin presentation layer, same rule as streamlit_app.py itself: this page
doesn't scrape anything directly. It launches
scripts/batch_scrape_metros.py as a background OS process and shows you
its log -- the real logic (checkpointing, resumability, per-metro
publishing) lives there, not here, so the CLI and this page can never
drift apart.

Runs as a background *process*, not a Streamlit-internal loop, on purpose:
a multi-metro batch can run for hours, and Streamlit's own execution model
isn't built for a single script run to block that long. Because it's a
real separate process, it keeps running even if you close this browser
tab -- only stopping this Streamlit server itself (or clicking "Stop
batch" below) stops it.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import streamlit as st

from bbb_scraper.config import settings
from bbb_scraper.logging_setup import configure_logging
from bbb_scraper.reference.metros import MetroDirectory

configure_logging()
st.set_page_config(page_title="Batch Scraper", page_icon="\U0001F5C3", layout="wide")

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = REPO_ROOT / "logs" / "batch"
LOG_DIR.mkdir(parents=True, exist_ok=True)

st.title("Batch Scraper")
st.caption(
    "Run one industry across many metros in one sitting -- each metro is "
    "checkpointed as it finishes (data/processed/batch/) and published "
    "straight to the static site, so a long batch is never all-or-nothing. "
    "Re-running with an overlapping metro list skips whatever's already "
    "done automatically."
)

metro_directory = MetroDirectory.load()
metro_options = {m.id: m.name for m in metro_directory.all()}

if "batch_process" not in st.session_state:
    st.session_state.batch_process = None
    st.session_state.batch_log_path = None
    st.session_state.batch_cmd = None


def _is_running() -> bool:
    proc = st.session_state.batch_process
    return proc is not None and proc.poll() is None


with st.form("batch_form"):
    industry_text = st.text_input(
        "Industry / category", placeholder="e.g. Car Dealers, Roofing Contractors, CPA"
    )
    st.caption("Sent as-is to BBB's search, same as the main search page -- most industry phrasing works.")

    selected_metro_ids = st.multiselect(
        "Metros", options=list(metro_options.keys()),
        format_func=lambda mid: metro_options[mid],
        help="Runs one after another, not in parallel -- picking more metros means "
             "a longer batch, not a faster one.",
    )
    run_all = st.checkbox(f"...or run every metro ({len(metro_options)} total)", value=False)

    col1, col2, col3 = st.columns(3)
    radius = col1.number_input("Radius (mi)", min_value=1.0, value=40.0, step=5.0)
    min_population = col2.number_input("Min population", min_value=0, value=25_000, step=5_000)
    pages_per_place = col3.number_input(
        "Pages/place", min_value=1, max_value=settings.bbb_max_search_pages, value=15,
    )

    fetch_details = st.checkbox(
        "Fetch full contact details for every business",
        value=False,
        help="Off by default -- roughly doubles time per metro. For a first broad "
             "pass, breadth (more metros) usually matters more than depth (every "
             "owner/contact) -- you can always re-run a specific metro with this on "
             "later once you know which ones matter.",
    )
    force = st.checkbox(
        "Redo metros already run for this exact industry", value=False,
        help="Off by default -- a metro already checkpointed for this industry is "
             "skipped, so it's always safe to add more metros to a previous batch "
             "without repeating finished work.",
    )
    submitted = st.form_submit_button(
        "Start batch", type="primary", use_container_width=True, disabled=_is_running()
    )

if submitted and not _is_running():
    if not industry_text.strip():
        st.error("Enter an industry.")
        st.stop()
    if not selected_metro_ids and not run_all:
        st.error('Select at least one metro (or check "run every metro").')
        st.stop()

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = LOG_DIR / f"batch-{timestamp}.log"

    cmd = [
        sys.executable, str(REPO_ROOT / "scripts" / "batch_scrape_metros.py"),
        "--industry", industry_text.strip(),
        "--radius", str(radius), "--min-population", str(int(min_population)),
        "--pages-per-place", str(int(pages_per_place)),
    ]
    cmd += ["--all-metros"] if run_all else ["--metros", ",".join(selected_metro_ids)]
    if fetch_details:
        cmd.append("--details")
    if force:
        cmd.append("--force")

    log_file = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, cwd=str(REPO_ROOT))
    st.session_state.batch_process = process
    st.session_state.batch_log_path = log_path
    st.session_state.batch_cmd = cmd
    st.rerun()

st.divider()

if st.session_state.batch_log_path is not None:
    log_path = st.session_state.batch_log_path
    running = _is_running()

    status_col, stop_col = st.columns([5, 1])
    with status_col:
        if running:
            st.info(
                "Batch running -- this page refreshes itself every few seconds. "
                "Safe to close this tab; the process keeps going as long as the "
                f"Streamlit server itself stays running. Log file: `{log_path}`"
            )
        else:
            st.success(f"Not currently running (finished, stopped, or not started this session). Log file: `{log_path}`")
    with stop_col:
        if running and st.button("Stop batch"):
            st.session_state.batch_process.terminate()
            st.rerun()

    log_text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    st.code(log_text or "(no output yet)", language=None)

    if running:
        time.sleep(3)
        st.rerun()
elif not submitted:
    st.info("Fill in the form above and click **Start batch** to get going.")

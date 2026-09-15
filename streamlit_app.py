"""
BBB batch scraper -- the only UI this project ships.

Run locally:
    streamlit run streamlit_app.py      (or double-click run_streamlit.bat)

This is a personal control panel, not a public app: clicking "Start batch"
launches real scraping through your real proxy / BBB session, and (unless
you turn it off) real Yelp API calls. Keep it private -- there's no auth.

Thin presentation layer, on purpose: this page doesn't scrape anything
itself. It launches scripts/batch_scrape_metros.py as a background OS
process and tails its log. All the real logic -- the metro sweep,
checkpointing/resume, Yelp enrichment + quota handling, per-metro site
publish -- lives in that script, so the CLI and this page can't drift.

**Robustness rework, 2026-09-11**, after a real incident: two overnight-
scale `--details` batches (Roof Contractors/Atlanta, then Electricians/
Dallas) each ran 65-78 minutes -- thousands of successful requests -- then
vanished mid-run with zero checkpoint, zero exception in the log, just a
silent cutoff. Root cause, confirmed from the logs themselves: the batch
subprocess was spawned as a plain child that shares this Streamlit
process's console/process group, AND this page re-read the *entire*,
ever-growing log file into memory and re-rendered it every ~3-second
auto-refresh -- for a batch running long enough, that log grows into the
hundreds of KB to multi-MB, making each refresh slower than the last until
the page looks and feels frozen. The likely chain: the page felt frozen ->
the terminal/Streamlit process got closed or restarted to "fix" it -> the
still-running batch subprocess, sharing that console, died with it,
instantly, with nothing left to flush. Three independent fixes:
  1. The log view now tails a bounded window of the file (LOG_TAIL_BYTES),
     never the whole thing -- constant cost per refresh no matter how long
     the batch has been running.
  2. The child process is launched fully detached (Windows:
     CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS, its own stdin) so it does
     NOT share this process's console -- closing this tab, closing the
     terminal, or restarting Streamlit itself no longer touches it. Only
     the "Stop batch" button (or killing its PID directly) ends it now,
     matching what the old docstring already *claimed* but didn't actually
     guarantee.
  3. Because a Streamlit restart drops this page's in-memory
     `st.session_state` (the Popen handle included), the running batch's
     pid/log path are mirrored to logs/batch/current_run.json so a fresh
     session can find and reattach to a still-running batch instead of
     showing "not running" and inviting a second, racing one to start
     against the same metros.
Belt-and-suspenders on the scraper side too: scripts/batch_scrape_metros.py
now writes a recoverable partial snapshot every 25 businesses during a long
--details loop, so even a genuine hard kill (sleeping laptop, Task Manager,
a power blip) loses at most a few minutes of progress, not the whole metro.

**Progress UI replaced, 2026-09-13.** Even a bounded log tail is still a
wall of raw "GET https://..." request lines -- Nick's ask, after the above
incident (and a second one, unrelated to the log, that still made him
distrust it): a clean per-metro checklist instead, not scrolling text. The
launched script now also gets --progress-file <path>, and writes structured
JSON (status/counts per metro, see _write_progress in batch_scrape_metros.py)
that this page polls and renders as a progress bar + running totals + one
line per metro (waiting / running / done with its numbers / failed with why)
via st.status(). The raw log still exists (nothing about logging itself was
removed -- it's still the thing a real bug gets traced back through) but
now lives inside a collapsed "Full log" expander, off by default.

**Dead-website check wired in, 2026-09-14** (Nick's ask: fully integrated,
on by default, unproxied). "Check for dead/parked websites" launches the
batch with --check-websites (see bbb_scraper/webcheck and
batch_scrape_metros.py's _check_metro_websites) -- a per-metro dead-website
count shows up in both the aggregate metrics row and each metro's own
status line, same best-effort/never-fatal treatment as Yelp enrichment.

**Interface simplified, 2026-09-14** (Nick's ask: fewer decisions, always
the recommended settings). Two changes:
  1. Industry is now `st.selectbox(..., accept_new_options=True)` seeded
     with Angi's 167 companylist categories (data/reference/
     angi_categories.json) instead of a bare text_input -- picking one
     guarantees a match once Angi cross-referencing is built; typing
     anything else still works exactly as before for BBB/Yelp. See
     data/reference/README.md's angi_categories.json section for why this
     list is narrower than BBB's own taxonomy (Angi is home-services only).
  2. Radius/min-population/pages-per-place/full-detail, and (as of a
     same-day follow-up ask) Yelp enrichment/dead-website check/live
     deploy, are no longer user-facing controls -- always run with
     ENFORCED_* below, all of them on. There's no real tradeoff being
     hidden on pages-per-place specifically: BBB caps pagination at
     settings.bbb_max_search_pages regardless of what's requested (see
     etl/extract.py), so a lower value only ever means fewer results for
     no benefit; the other three were already best-effort/never-fatal
     (a missing Yelp key, a webcheck failure, or no AWS CLI configured
     just means that piece is skipped for the run, not an error), so
     forcing them on risks nothing a checkbox was actually protecting
     against. scripts/batch_scrape_metros.py's own CLI flags are
     untouched -- still there for direct script use, just no longer
     exposed as choices here. The only remaining checkbox is "redo metros
     already run" -- a real per-run decision (whether to overwrite
     existing checkpoints), not a "which settings are best" question.

**Angi wired into this same button, 2026-09-15** (previously only reachable
via a separate standalone script, scripts/run_batch_with_angi.py, now
retired). Same treatment as Yelp/webcheck: always on, best-effort, no new
checkbox -- the industry field already doubles as the Angi category picker
(it's seeded from Angi's own category list), so no new form field either.
Runs concurrently with BBB inside batch_scrape_metros.py, not after it --
see that script's module docstring for exactly which steps run in parallel
and which stay sequential (and why).
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import streamlit as st

from bbb_scraper.config import settings
from bbb_scraper.logging_setup import configure_logging
from bbb_scraper.reference.categories import CategoryDirectory
from bbb_scraper.reference.metros import MetroDirectory

configure_logging()
st.set_page_config(page_title="BBB Batch Scraper", page_icon="\U0001F5C3", layout="wide")

REPO_ROOT = Path(__file__).resolve().parent
LOG_DIR = REPO_ROOT / "logs" / "batch"
LOG_DIR.mkdir(parents=True, exist_ok=True)
RUN_STATE_PATH = LOG_DIR / "current_run.json"

# Bytes of the log file to read+render per auto-refresh -- see the module
# docstring. Keeps every refresh's cost constant regardless of how long the
# batch has been running (the bug this fixes: reading a multi-MB file whole,
# every ~3s, made the page progressively slower over the course of a batch).
LOG_TAIL_BYTES = 12_000

# Enforced batch settings, 2026-09-14 -- see the module docstring's
# "Interface simplified" entry. No longer user-facing choices: every batch
# always runs with these, rather than depending on whatever a particular
# run's form happened to have set.
ENFORCED_RADIUS_MILES = 15.0
ENFORCED_MIN_POPULATION = 40_000
# BBB's own hard ceiling, not a courtesy limit -- it caps totalPages at this
# regardless of what's requested (confirmed in etl/extract.py), so there's
# no lower value that would ever help; always ask for the max.
ENFORCED_PAGES_PER_PLACE = settings.bbb_max_search_pages


def _tail_file(path: Path, max_bytes: int = LOG_TAIL_BYTES) -> tuple[str, bool]:
    """Read at most the last `max_bytes` of `path` without loading the whole
    file into memory -- cheap to call every rerun even once a log is
    multi-megabyte. Returns (text, truncated)."""
    size = path.stat().st_size
    with path.open("rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
            f.readline()  # drop the partial first line from the mid-file seek
            return f.read().decode("utf-8", errors="replace"), True
        return f.read().decode("utf-8", errors="replace"), False


def _progress_path_for(log_path: Path) -> Path:
    """Deterministic transform (same suffix swap on both the launch side and
    the reattach side) so current_run.json doesn't need its own separate
    field for this -- one less thing that could drift out of sync."""
    return log_path.with_suffix(".progress.json")


def _read_progress(path: Path) -> dict | None:
    """None on anything short of a clean read -- missing (batch hasn't
    written its first snapshot yet), or a half-written/corrupt file (should
    be rare given _write_progress's atomic replace, but a poller reading
    exactly the wrong instant is cheap to just tolerate here) -- either way
    the caller falls back to the raw log tail rather than erroring."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _pid_alive(pid: int) -> bool:
    """True if `pid` is a live process. Works for a PID this session never
    itself spawned (e.g. reattaching via current_run.json after a Streamlit
    restart), unlike a held Popen handle's `.poll()`.

    On Windows, a bare `OpenProcess` success is NOT enough -- confirmed by a
    real test that first looked right and then wasn't: the process object
    behind a PID can outlive the process itself as long as *any* handle to
    it (even one held by an unrelated process, e.g. an unwaited Popen
    object) is still open, so `OpenProcess` alone can report a just-killed
    PID as still openable. `GetExitCodeProcess`'s STILL_ACTIVE is the real
    liveness check.
    """
    if sys.platform == "win32":
        process_query_limited_information = 0x1000
        still_active = 259
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _kill_pid(pid: int) -> None:
    """Kill a batch we're only reattached to (no Popen handle to .terminate()
    this session). `/T` also takes down anything it spawned (e.g. an AWS CLI
    deploy call in flight)."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
    else:
        import signal
        os.kill(pid, signal.SIGTERM)


def _write_run_state(pid: int, log_path: Path, cmd: list[str]) -> None:
    RUN_STATE_PATH.write_text(
        json.dumps({"pid": pid, "log_path": str(log_path), "cmd": cmd, "started_at": time.time()}),
        encoding="utf-8",
    )


def _clear_run_state() -> None:
    RUN_STATE_PATH.unlink(missing_ok=True)


def _read_run_state() -> dict | None:
    if not RUN_STATE_PATH.exists():
        return None
    try:
        return json.loads(RUN_STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


st.title("BBB Batch Scraper")
st.caption(
    "Run one industry across many metros in one sitting. Each metro is BBB-swept and "
    "Angi-scraped concurrently, matched against a Yelp Fusion search and Angi by phone, "
    "and checkpointed to `data/processed/batch/` as it finishes -- a long batch is never "
    "all-or-nothing, and re-running with an overlapping metro list skips whatever's already done."
)

metro_directory = MetroDirectory.load()
metro_options = {m.id: m.name for m in metro_directory.all()}

angi_directory = CategoryDirectory.load(settings.angi_categories_file)
angi_category_names = sorted(c.name for c in angi_directory.all())

if "batch_process" not in st.session_state:
    st.session_state.batch_process = None  # a Popen handle, only if *this* session spawned it
    st.session_state.batch_pid = None      # pid, whether owned or reattached
    st.session_state.batch_log_path = None
    st.session_state.batch_cmd = None

    # Fresh session (new browser tab, or Streamlit itself restarted) -- if
    # current_run.json says a batch is mid-flight, reattach instead of
    # silently forgetting about it (which is what used to make a genuinely
    # still-running batch look stopped, and invited starting a second one
    # against the same metros).
    _state = _read_run_state()
    if _state and _pid_alive(_state["pid"]):
        st.session_state.batch_pid = _state["pid"]
        st.session_state.batch_log_path = Path(_state["log_path"])
        st.session_state.batch_cmd = _state.get("cmd")
    elif _state:
        _clear_run_state()  # stale -- whatever pid this pointed at is gone


def _render_progress(progress: dict) -> None:
    """Clean per-metro checklist -- a progress bar, running totals, and one
    line per metro -- instead of a scrolling raw log. Streamlit's own
    st.status() widget already draws exactly the spinner/checkmark/error
    look this needs; no extra charting library required for something this
    simple. Called fresh every ~3s rerun, so it just renders whatever the
    latest progress.json snapshot says -- no state of its own to manage.
    """
    metros = progress.get("metros") or []
    total = progress.get("total_metros") or len(metros) or 1
    settled = sum(1 for m in metros if m["status"] in ("done", "skipped", "failed"))
    st.progress(min(1.0, settled / total))

    biz_total = sum(m.get("businesses") or 0 for m in metros if m["status"] == "done")
    yelp_total = sum(m.get("yelp_matched") or 0 for m in metros if m["status"] == "done")
    angi_total = sum(m.get("angi_matched") or 0 for m in metros if m["status"] == "done")
    mapquest_reviews_total = sum(m.get("mapquest_reviews") or 0 for m in metros if m["status"] == "done")
    dead_total = sum(m.get("websites_dead") or 0 for m in metros if m["status"] == "done")
    failed_total = sum(1 for m in metros if m["status"] == "failed")
    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
    c1.metric("Metros", f"{settled}/{total}")
    c2.metric("Businesses scraped", f"{biz_total:,}")
    c3.metric("Matched to Yelp", f"{yelp_total:,}")
    c4.metric("Matched to Angi", f"{angi_total:,}")
    c5.metric("MapQuest reviews", f"{mapquest_reviews_total:,}")
    c6.metric("Dead websites", f"{dead_total:,}")
    c7.metric("Failed", failed_total)

    for m in metros:
        status = m["status"]
        if status == "pending":
            st.markdown(f"○ **{m['name']}** — waiting")
        elif status == "skipped":
            st.markdown(f"⏭️ **{m['name']}** — already done, skipped")
        elif status == "running":
            # NOT `with st.status(..., state="running"):` -- confirmed via
            # Streamlit's own source (StatusContainer.__exit__) that a `with`
            # block exiting normally force-flips state="running" to
            # "complete" on its way out, every time, no exception needed.
            # That's the right behavior for wrapping code that's actually
            # executing inside the block; it's wrong here, where the state
            # is just a snapshot read from progress.json -- it silently
            # made every currently-running metro render with the same
            # checkmark as a finished one. Calling st.status() as a plain
            # object (no `with`) never triggers __exit__, so "running"
            # actually stays running.
            box = st.status(f"{m['name']} — scraping...", state="running")
            box.write("BBB and Angi scraping concurrently, then matching Yelp, fetching contact details...")
        elif status == "done":
            bits = [f"{m['businesses']} businesses"]
            if m.get("yelp_matched") is not None:
                bits.append(f"{m['yelp_matched']} matched to Yelp")
            if m.get("angi_businesses") is not None:
                bits.append(f"{m['angi_businesses']} Angi ({m.get('angi_matched') or 0} matched)")
            if m.get("mapquest_matched") is not None:
                bits.append(f"{m['mapquest_matched']} MapQuest ({m.get('mapquest_reviews') or 0} reviews)")
            if m.get("websites_dead") is not None:
                bits.append(f"{m['websites_dead']} dead websites")
            if m.get("top_lead_score"):
                bits.append(f"top lead {m['top_lead_score']}")
            if m.get("elapsed_s"):
                bits.append(f"{round(m['elapsed_s'] / 60)}m")
            box = st.status(f"{m['name']} — done", state="complete")
            box.write(", ".join(bits))
        elif status == "failed":
            box = st.status(f"{m['name']} — failed", state="error", expanded=True)
            box.write(m.get("error") or "See the full log below for details.")


def _is_running() -> bool:
    proc = st.session_state.batch_process
    if proc is not None:
        return proc.poll() is None
    pid = st.session_state.batch_pid
    return pid is not None and _pid_alive(pid)


with st.form("batch_form"):
    industry_text = st.selectbox(
        "Industry / category",
        options=angi_category_names,
        index=None,
        accept_new_options=True,
        placeholder="Pick a category, or type your own -- e.g. Car Dealers, CPA",
        help=(
            f"Pre-loaded with Angi's own {len(angi_category_names)} home-services "
            "categories (plumbing, HVAC, roofing, landscaping, real estate agents, "
            "...) -- picking one guarantees a match once Angi cross-referencing is "
            "built. Angi doesn't cover every industry (no Dentists, Car Dealers, "
            "etc.) -- type anything else and it still works fine for BBB and Yelp, "
            "just without a guaranteed Angi match."
        ),
    )
    st.caption("Sent as-is to BBB's search (and used as the Yelp search term) -- most phrasing works.")

    selected_metro_ids = st.multiselect(
        "Metros", options=list(metro_options.keys()),
        format_func=lambda mid: metro_options[mid],
        help="Run one after another, not in parallel -- more metros means a longer "
             "batch, not a faster one.",
    )
    run_all = st.checkbox(f"...or run every metro ({len(metro_options)} total)", value=False)

    st.caption(
        f"Every run: {ENFORCED_RADIUS_MILES:g}mi radius, "
        f"{ENFORCED_MIN_POPULATION:,}+ population, full contact details, "
        f"up to {ENFORCED_PAGES_PER_PLACE} pages/place (BBB's own max), "
        "Yelp enrichment, Angi enrichment (concurrent with BBB, matched by phone), "
        "MapQuest review capture (real Yelp-sourced review text/rating/date per business), "
        "dead-website check, and live deploy as each metro "
        "finishes -- no longer per-run choices, see the Configuration section below."
    )

    force = st.checkbox(
        "Redo metros already run for this exact industry", value=False,
        help="Off by default -- a metro already checkpointed for this industry is "
             "skipped, so it's always safe to add more metros to a previous batch.",
    )
    submitted = st.form_submit_button(
        "Start batch", type="primary", use_container_width=True, disabled=_is_running()
    )

if submitted and not _is_running():
    if not industry_text or not industry_text.strip():
        st.error("Enter or select an industry.")
        st.stop()
    if not selected_metro_ids and not run_all:
        st.error('Select at least one metro (or check "run every metro").')
        st.stop()

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = LOG_DIR / f"batch-{timestamp}.log"
    progress_path = _progress_path_for(log_path)

    cmd = [
        sys.executable, "-u", str(REPO_ROOT / "scripts" / "batch_scrape_metros.py"),
        "--industry", industry_text.strip(),
        "--radius", str(ENFORCED_RADIUS_MILES), "--min-population", str(ENFORCED_MIN_POPULATION),
        "--pages-per-place", str(ENFORCED_PAGES_PER_PLACE),
        "--details", "--yelp", "--angi", "--mapquest", "--check-websites", "--deploy",
        "--progress-file", str(progress_path),
    ]
    cmd += ["--all-metros"] if run_all else ["--metros", ",".join(selected_metro_ids)]
    if force:
        cmd.append("--force")

    log_file = log_path.open("w", encoding="utf-8")
    popen_kwargs: dict = {}
    if sys.platform == "win32":
        # Fully detach: no shared console with this Streamlit process, so
        # the child is unaffected by closing this tab, closing the terminal
        # Streamlit runs in, or Streamlit itself being stopped/restarted.
        # See the module docstring for the real incident this fixes.
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    process = subprocess.Popen(
        cmd, stdout=log_file, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        cwd=str(REPO_ROOT), **popen_kwargs,
    )
    st.session_state.batch_process = process
    st.session_state.batch_pid = process.pid
    st.session_state.batch_log_path = log_path
    st.session_state.batch_cmd = cmd
    _write_run_state(process.pid, log_path, cmd)
    st.rerun()

with st.expander("Configuration"):
    st.write(
        f"**Search settings (fixed, not per-run):** {ENFORCED_RADIUS_MILES:g}mi radius, "
        f"{ENFORCED_MIN_POPULATION:,}+ minimum city population, full BBB contact details "
        f"always fetched, up to {ENFORCED_PAGES_PER_PLACE} pages per place (BBB's own cap, "
        "not a choice below it), Yelp enrichment, Angi enrichment, dead-website check, and "
        "live deploy all always on -- see the module docstring's 2026-09-14/2026-09-15 entries."
    )
    st.write(f"**Proxy:** {'enabled' if settings.proxy_enabled else 'disabled'}"
             + (f" ({settings.proxy_host})" if settings.proxy_enabled else ""))
    st.write(f"**Impersonation:** `{settings.http_impersonate}`")
    st.write(f"**BBB session file:** "
             f"{'found' if settings.bbb_session_file.exists() else 'not found (optional)'}")
    st.write(f"**Yelp API key:** {'set' if settings.yelp_api_key else 'not set (Yelp enrichment will be skipped)'}")
    st.write(
        "**Angi:** runs concurrently with BBB, matched by phone -- the industry field above "
        "doubles as the Angi category (an exact match from the dropdown always resolves; "
        "free text resolves only if it happens to match an Angi category name, otherwise "
        "Angi enrichment is skipped for that run, same as a missing Yelp key)."
    )
    st.write(f"**Output sinks:** {', '.join(settings.output_sink_names)}")

st.divider()

if st.session_state.batch_log_path is not None:
    log_path = st.session_state.batch_log_path
    running = _is_running()
    reattached = running and st.session_state.batch_process is None

    status_col, stop_col = st.columns([5, 1])
    with status_col:
        if running:
            st.info(
                ("Batch running (reattached after a page/server restart -- still the same "
                 "process, nothing was lost). " if reattached else "Batch running. ")
                + "This page refreshes itself every few seconds. The batch is a fully "
                "detached process, so it keeps running even if this tab, its terminal, or "
                f"Streamlit itself closes or restarts. Log file: `{log_path}`"
            )
        else:
            st.success(f"Not currently running (finished, stopped, or not started this session). Log file: `{log_path}`")
    with stop_col:
        if running and st.button("Stop batch"):
            if st.session_state.batch_process is not None:
                st.session_state.batch_process.terminate()
            elif st.session_state.batch_pid is not None:
                _kill_pid(st.session_state.batch_pid)
            _clear_run_state()
            st.rerun()

    progress = _read_progress(_progress_path_for(log_path))
    if progress is not None:
        _render_progress(progress)
    elif running:
        st.caption("Starting up -- the first progress update lands once BBB search results start coming back.")

    with st.expander("Full log (for troubleshooting)", expanded=progress is None):
        if log_path.exists():
            text, truncated = _tail_file(log_path)
            if truncated:
                st.caption(
                    f"Showing the last ~{LOG_TAIL_BYTES // 1000}KB of a larger log file "
                    f"(full file: `{log_path}`)."
                )
            st.code(text or "(no output yet)", language=None)
            if not running:
                # Only offered once the batch is done -- while it's still
                # running this would re-read the whole (possibly multi-MB and
                # growing) file into memory every ~3s refresh, exactly the
                # cost the tail view above exists to avoid.
                st.download_button("Download full log", data=log_path.read_bytes(), file_name=log_path.name)
        else:
            st.code("(no output yet)", language=None)

    if running:
        time.sleep(3)
        st.rerun()
    elif RUN_STATE_PATH.exists():
        # Finished/crashed on its own (not via the Stop button) since we last
        # checked -- clear the stale pointer so a future session doesn't
        # chase a dead pid.
        _clear_run_state()
elif not submitted:
    st.info("Fill in the form above and click **Start batch** to get going.")

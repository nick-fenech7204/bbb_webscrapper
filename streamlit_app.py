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
    "Run one industry across many metros in one sitting. Each metro is BBB-swept, "
    "matched against a Yelp Fusion search, and checkpointed to "
    "`data/processed/batch/` as it finishes -- a long batch is never all-or-nothing, "
    "and re-running with an overlapping metro list skips whatever's already done."
)

metro_directory = MetroDirectory.load()
metro_options = {m.id: m.name for m in metro_directory.all()}

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


def _is_running() -> bool:
    proc = st.session_state.batch_process
    if proc is not None:
        return proc.poll() is None
    pid = st.session_state.batch_pid
    return pid is not None and _pid_alive(pid)


with st.form("batch_form"):
    industry_text = st.text_input(
        "Industry / category", placeholder="e.g. Car Dealers, Roofing Contractors, CPA"
    )
    st.caption("Sent as-is to BBB's search (and used as the Yelp search term) -- most phrasing works.")

    selected_metro_ids = st.multiselect(
        "Metros", options=list(metro_options.keys()),
        format_func=lambda mid: metro_options[mid],
        help="Run one after another, not in parallel -- more metros means a longer "
             "batch, not a faster one.",
    )
    run_all = st.checkbox(f"...or run every metro ({len(metro_options)} total)", value=False)

    col1, col2, col3 = st.columns(3)
    radius = col1.number_input("Radius (mi)", min_value=1.0, value=40.0, step=5.0)
    min_population = col2.number_input("Min population", min_value=0, value=25_000, step=5_000)
    pages_per_place = col3.number_input(
        "Pages/place", min_value=1, max_value=settings.bbb_max_search_pages, value=15,
    )

    enrich_yelp = st.checkbox(
        "Enrich with Yelp (~5 API calls per metro)",
        value=True,
        help="Runs one Yelp Fusion search per metro and matches it to the BBB rows, "
             "adding the matched yelp_name/rating/review_count/url + derived-intelligence "
             "columns to the checkpoint and to the site's Intelligence view. Best-effort: "
             "no API key, a low daily quota (free tier is 300/day), or a failed call just "
             "means BBB-only output for the rest of the batch -- never an error.",
    )
    fetch_details = st.checkbox(
        "Fetch full BBB contact details for every business",
        value=False,
        help="Off by default -- roughly doubles time per metro. Breadth (more metros) "
             "usually matters more than depth for a first pass; re-run a specific metro "
             "with this on later. A big metro with this on can run well over an hour -- "
             "progress is now snapshotted every 25 businesses to data/processed/batch/"
             "_partial/ so a crash mid-metro can't lose the whole thing (see the module "
             "docstring in scripts/batch_scrape_metros.py).",
    )
    deploy_when_done = st.checkbox(
        "Deploy each metro to the live site as it finishes",
        value=True,
        help="Runs scripts/deploy_site.py (S3 sync + CloudFront invalidation) right after "
             "each metro publishes locally -- live within seconds, not held back until the "
             "whole batch finishes. Needs the AWS CLI configured locally -- if it isn't, this "
             "is reported but the batch still finishes normally; the scrape and the local "
             "site/data/ files are unaffected either way. Uncheck to only publish locally and "
             "deploy by hand later.",
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
    if not industry_text.strip():
        st.error("Enter an industry.")
        st.stop()
    if not selected_metro_ids and not run_all:
        st.error('Select at least one metro (or check "run every metro").')
        st.stop()

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = LOG_DIR / f"batch-{timestamp}.log"

    cmd = [
        sys.executable, "-u", str(REPO_ROOT / "scripts" / "batch_scrape_metros.py"),
        "--industry", industry_text.strip(),
        "--radius", str(radius), "--min-population", str(int(min_population)),
        "--pages-per-place", str(int(pages_per_place)),
    ]
    cmd += ["--all-metros"] if run_all else ["--metros", ",".join(selected_metro_ids)]
    cmd.append("--yelp" if enrich_yelp else "--no-yelp")
    cmd.append("--deploy" if deploy_when_done else "--no-deploy")
    if fetch_details:
        cmd.append("--details")
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
    st.write(f"**Proxy:** {'enabled' if settings.proxy_enabled else 'disabled'}"
             + (f" ({settings.proxy_host})" if settings.proxy_enabled else ""))
    st.write(f"**Impersonation:** `{settings.http_impersonate}`")
    st.write(f"**BBB session file:** "
             f"{'found' if settings.bbb_session_file.exists() else 'not found (optional)'}")
    st.write(f"**Yelp API key:** {'set' if settings.yelp_api_key else 'not set (Yelp enrichment will be skipped)'}")
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
            # growing) file into memory every ~3s refresh, exactly the cost
            # the tail view above exists to avoid.
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

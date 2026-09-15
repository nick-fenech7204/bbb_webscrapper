#!/usr/bin/env python
"""
Run a full BBB + Angi batch for one industry across several metros, with
Angi scraping happening *concurrently* with the BBB scrape rather than
waiting for it to finish first -- they hit completely unrelated sites
(bbb.org vs angi.com), so there's no reason to serialize them.

    python scripts/run_batch_with_angi.py --industry "Roofing" \\
        --metros phoenix-az,nashville-tn

Steps:
  1. Launch the BBB batch (scripts/batch_scrape_metros.py, all metros in
     one call -- it already supports comma-separated --metros) as a
     background subprocess.Popen -- non-blocking, returns immediately.
  2. While that's in flight, scrape Angi for each metro in turn
     (sequential *between themselves* -- no need to run two Angi scrapers
     at once just because BBB is busy elsewhere; that's still one host
     getting hit by one scraper at a time).
  3. Wait for the BBB subprocess too (it's usually the slower side,
     especially with --details, which this always passes -- see
     streamlit_app.py's ENFORCED_* for why full detail is always on).
  4. For each metro: enrich its BBB checkpoint with its Angi CSV
     (bbb_scraper/angi/enrich.py), then republish (--master).
  5. Deploy once at the end, covering every metro in the batch.

**A real bug, found running this the first time (2026-09-14), worth
knowing before launching this in the background again:** wrapping a
command in your own `nohup ... & disown` when you've *also* asked a
background-capable tool to detach it double-backgrounds it -- on Windows/
Git-Bash this produced two independent copies of the same run racing each
other (confirmed: two distinct process trees, each with its own real
subprocess children, not just a process-accounting artifact), and
corrupted one metro's Angi output (both copies opened the same CSV in
write-truncate mode; whichever finished last silently erased the other's
progress). This script manages its own concurrency internally via
subprocess.Popen -- launch *it* the plain way (however your background
mechanism backgrounds a single command), with no extra `nohup`/`&`/
`disown` of your own around it.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for publish_site_data below

from publish_site_data import slugify

from bbb_scraper.config import settings
from bbb_scraper.reference.categories import CategoryDirectory
from bbb_scraper.reference.metros import MetroDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def _resolve_angi_category(name: str) -> tuple[str, str]:
    """Industry display name -> (slug, canonical Angi display name). Exits
    with a clear message on no/ambiguous match rather than silently
    guessing at a slug (see data/reference/README.md -- Angi slugs aren't
    a guessable slugification of the label)."""
    directory = CategoryDirectory.load(settings.angi_categories_file)
    matches = directory.search(name)
    if not matches:
        print(f"No Angi category matches {name!r} -- see data/reference/angi_categories.json. "
              "Angi-side enrichment isn't possible for this industry; run scripts/batch_scrape_metros.py "
              "directly instead of this script.")
        sys.exit(1)
    if len(matches) > 1:
        print(f"{name!r} matched {len(matches)} Angi categories -- pass --angi-category-name more specifically:")
        for c in matches:
            print(f"  {c.name!r}")
        sys.exit(1)
    return matches[0].slug, matches[0].name


def _angi_state_city(metro_id: str) -> tuple[str, str]:
    """"phoenix-az" -> ("az", "phoenix"); "san-antonio-tx" -> ("tx", "san-antonio").
    A metro id's last hyphen-separated segment is always the 2-letter
    state (this project's own data/reference/metros.json convention) --
    Angi's own city slug is usually the same spelling, but isn't
    guaranteed to be (confirm on a real 404 before assuming; see
    bbb_scraper/angi/scraper.py's docstring)."""
    city, _, state = metro_id.rpartition("-")
    return state, city


def run(cmd: list, label: str) -> subprocess.CompletedProcess:
    print(f"\n=== {label} ===", flush=True)
    print("$", " ".join(str(c) for c in cmd), flush=True)
    started = time.time()
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    elapsed = time.time() - started
    tail = "\n".join((result.stdout or "").strip().splitlines()[-6:])
    print(tail or "(no stdout)", flush=True)
    print(f"[{label}] exit={result.returncode} elapsed={elapsed / 60:.1f}min", flush=True)
    if result.returncode != 0:
        print("STDERR TAIL:", (result.stderr or "")[-1500:], flush=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--industry", required=True, help='BBB search term, e.g. "Roofing"')
    parser.add_argument("--metros", required=True, help="Comma-separated metro ids, e.g. phoenix-az,nashville-tn")
    parser.add_argument("--angi-category-name", default=None,
                         help="Angi category display name if different from --industry (default: same as --industry)")
    parser.add_argument("--angi-max-businesses", type=int, default=150)
    parser.add_argument("--radius", type=float, default=15.0)
    parser.add_argument("--min-population", type=int, default=40_000)
    parser.add_argument("--pages-per-place", type=int, default=15)
    parser.add_argument("--deploy", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    metro_ids = [m.strip() for m in args.metros.split(",") if m.strip()]
    metro_directory = MetroDirectory.load()
    metros = []
    for mid in metro_ids:
        metro = metro_directory.get(mid)
        if metro is None:
            print(f"Unknown metro id {mid!r} -- see data/reference/metros.json")
            return 1
        metros.append((mid, metro.name))

    angi_category_slug, angi_category_label = _resolve_angi_category(args.angi_category_name or args.industry)
    industry_slug = slugify(args.industry)
    print(f"Industry: {args.industry!r} | Angi category: {angi_category_label!r} ({angi_category_slug}) | "
          f"metros: {[m[1] for m in metros]}", flush=True)

    # --- step 1: BBB batch, launched non-blocking -- Angi below runs while this is in flight ---
    bbb_log_path = REPO_ROOT / "logs" / "run_batch_with_angi_bbb.log"
    bbb_log = bbb_log_path.open("w", encoding="utf-8")
    bbb_cmd = [
        PY, "-u", "scripts/batch_scrape_metros.py",
        "--industry", args.industry,
        "--metros", ",".join(metro_ids),
        "--radius", str(args.radius), "--min-population", str(args.min_population),
        "--pages-per-place", str(args.pages_per_place),
        "--details", "--yelp", "--check-websites", "--no-deploy",
        "--progress-file", str(REPO_ROOT / "logs" / "run_batch_with_angi_progress.json"),
    ]
    print("\n=== BBB batch (started in background, Angi runs concurrently) ===", flush=True)
    print("$", " ".join(bbb_cmd), flush=True)
    bbb_process = subprocess.Popen(bbb_cmd, cwd=REPO_ROOT, stdout=bbb_log, stderr=subprocess.STDOUT)

    # --- step 2: Angi, one metro at a time, while BBB is still running ---
    angi_csvs: dict[str, Path] = {}
    for metro_id, metro_name in metros:
        state, city = _angi_state_city(metro_id)
        out = REPO_ROOT / "data" / "processed" / "angi" / f"{angi_category_slug}--{metro_id}.csv"
        if out.exists():
            # Mirrors batch_scrape_metros.py's own "skip a metro that
            # already has a checkpoint" behavior -- safe to re-run this
            # whole script over a partially-done batch without re-spending
            # real requests on a metro that already succeeded.
            print(f"\n=== Angi scrape: {metro_name} -- {out} already exists, skipping ===", flush=True)
            angi_csvs[metro_id] = out
            continue
        result = run(
            [
                PY, "scripts/scrape_angi_category.py",
                "--state", state, "--city", city, "--category", angi_category_slug,
                "--metro-label", metro_name, "--max-businesses", str(args.angi_max_businesses),
                "--no-use-proxy",  # Decodo sticky-session outage, see bbb_scraper/scraping/proxies.py
                "--output", str(out),
            ],
            f"Angi scrape: {metro_name}",
        )
        angi_csvs[metro_id] = out if result.returncode == 0 else None

    # --- step 3: make sure BBB is done too before enriching ---
    print("\n=== Waiting for BBB batch to finish (if it hasn't already) ===", flush=True)
    bbb_returncode = bbb_process.wait()
    bbb_log.close()
    print(f"BBB batch exit={bbb_returncode} -- see {bbb_log_path} for its full output", flush=True)
    if bbb_returncode != 0:
        print("BBB batch failed -- stopping before enrichment (needs its checkpoints).", flush=True)
        return 1

    # --- step 4: enrich + republish each metro ---
    for metro_id, metro_name in metros:
        bbb_csv = REPO_ROOT / "data" / "processed" / "batch" / f"{industry_slug}--{metro_id}.csv"
        angi_csv = angi_csvs.get(metro_id)
        if angi_csv and angi_csv.exists():
            run([PY, "scripts/enrich_with_angi.py", str(bbb_csv), str(angi_csv), "--in-place"],
                f"Enrich with Angi: {metro_name}")
        else:
            print(f"\n=== Skipping Angi enrichment for {metro_name} (scrape failed or produced nothing) ===",
                  flush=True)
        run([PY, "scripts/publish_site_data.py", str(bbb_csv), "--master",
             "--industry", args.industry, "--metro", metro_name],
            f"Publish: {metro_name}")

    if args.deploy:
        run([PY, "scripts/deploy_site.py"], "Deploy")
    print("\n=== Batch complete ===", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

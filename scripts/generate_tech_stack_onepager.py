#!/usr/bin/env python
"""
One-page overview of the technology used across this product and why each
piece is there -- for sharing with someone who wants "what's this built on"
without reading the code. Source of truth for what's actually used:
pyproject.toml (Python deps) + site/index.html (client-side libs) + each
package's own config in bbb_scraper/config.py -- re-check those if this
goes stale after a real stack change.

Needs the `reports` extra: pip install -e ".[reports]"

Usage:
    python scripts/generate_tech_stack_onepager.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Matches site/css/style.css's --accent -- same brand color as the other
# report PDFs in scripts/, not a separate palette.
ACCENT = (23, 97, 74)
TEXT = (27, 27, 25)
MUTED = (108, 108, 102)
BOX_BG = (243, 246, 244)


class OnePager(FPDF):
    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "", 7.5)
        self.set_text_color(*MUTED)
        self.cell(0, 8, "LossLess -- Tech Stack Overview", align="C")

    def h2(self, text):
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(*ACCENT)
        self.cell(0, 5.5, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(*ACCENT)
        self.set_line_width(0.5)
        y = self.get_y() + 0.4
        self.line(self.l_margin, y, self.l_margin + 20, y)
        self.ln(1.8)

    def row(self, tech, why, size=8.2):
        self.set_font("Helvetica", "B", size)
        self.set_text_color(*TEXT)
        w_label = 46
        x0 = self.get_x()
        self.multi_cell(w_label, 4.1, tech, new_x=XPos.RIGHT, new_y=YPos.TOP)
        self.set_xy(x0 + w_label, self.get_y())
        self.set_font("Helvetica", "", size)
        self.set_text_color(*MUTED)
        self.multi_cell(0, 4.1, why, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(0.8)


SECTIONS = [
    ("Collecting the data", [
        ("curl_cffi", "Impersonates a real browser's TLS/HTTP2 fingerprint -- plain requests/urllib3 get Cloudflare-blocked on BBB profile pages; this doesn't."),
        ("tenacity", "Automatic retry with exponential backoff on transient network/HTTP failures, so one flaky request doesn't end a run."),
        ("Decodo proxy", "Rotating residential IPs (bare, fresh connection per request) so BBB/Angi/MapQuest traffic doesn't all come from one address."),
        ("BeautifulSoup4 + lxml", "Parses the real HTML/JSON BBB, Angi, and MapQuest pages return into structured business records."),
    ]),
    ("Cross-referencing reputation sources", [
        ("Yelp Fusion API", "Official, rate-limited API (300 calls/day) -- star rating, review volume, matched to BBB by phone/name/geo."),
        ("Angi (angi.com)", "Scraped profile pages -- rating, review text (up to ~25 most recent per business), licenses, service categories."),
        ("MapQuest GraphQL", "Unauthenticated endpoint that surfaces real Yelp-sourced review text/date -- feeds sentiment analysis."),
    ]),
    ("Local AI", [
        ("Ollama (llama3.2)", "Runs on Nick's own gaming PC, not the cloud -- reads real review text, scores sentiment, and picks each business's top complaint. Zero API cost, nothing leaves the machine."),
    ]),
    ("Data pipeline", [
        ("Pydantic / pydantic-settings", "Typed data models and config -- catches a malformed record or missing setting immediately instead of silently downstream."),
        ("python-dotenv", "Keeps API keys and AWS/proxy credentials in a local .env file, out of the codebase."),
        ("CSV / JSON sinks", "No database -- every stage writes plain checkpoint files, so any run is inspectable and resumable by hand."),
    ]),
    ("Operator control panel", [
        ("Streamlit", "Internal-only batch-scraper UI (industry, metros, knobs, a live progress checklist). Never public-facing."),
    ]),
    ("Public lead-list site", [
        ("Plain HTML / CSS / JS", "No framework, no build step, no backend -- just reads pre-published JSON files. Nothing to compile, nothing to break."),
        ("SheetJS + jsPDF/autotable", "Client-side Excel and PDF export, loaded from cdnjs -- runs entirely in the rep's own browser."),
    ]),
    ("Hosting", [
        ("AWS S3 + CloudFront", "S3 stores the static site files privately; CloudFront serves them worldwide over HTTPS. Cheap, standard, no server to maintain."),
    ]),
    ("Keeping it reliable", [
        ("pytest (433 tests)", "Every parser, matcher, and scoring formula has real regression coverage, run before anything ships."),
        ("ruff", "Linting -- catches real bugs (unused variables, bad comparisons) as well as style."),
    ]),
]


def build(pdf: OnePager):
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 19)
    pdf.set_text_color(*ACCENT)
    pdf.cell(0, 9, "LossLess -- Tech Stack Overview", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(0, 4.6,
        "A BBB scraper + ETL pipeline that cross-references Yelp, Angi, and MapQuest, scores each business as a "
        "sales lead, and publishes curated lists to a public site. No database, no backend server, and no "
        "framework on the public site -- deliberately minimal, cheap to run, and easy for one person to maintain.",
        new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)

    for title, rows in SECTIONS:
        pdf.h2(title)
        for tech, why in rows:
            pdf.row(tech, why)
        pdf.ln(1.2)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "processed" / "tech_stack_onepager.pdf")
    args = ap.parse_args()

    pdf = OnePager()
    pdf.set_auto_page_break(auto=True, margin=13)
    pdf.set_margins(16, 14, 16)
    build(pdf)

    if pdf.page_no() != 1:
        raise SystemExit(f"Overflowed to {pdf.page_no()} pages -- trim content to fit one page.")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(args.out))
    print(f"Wrote {args.out} ({pdf.page_no()} page)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

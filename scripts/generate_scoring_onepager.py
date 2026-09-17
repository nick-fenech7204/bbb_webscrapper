#!/usr/bin/env python
"""
One-page reference card of the lead-scoring model's actual metrics and
weights -- every signal, band, bonus, and reachability rule on a single
page, no prose. For the plain-language pitch see
generate_executive_summary.py; for the full formula walkthrough + worked
example + flags + design philosophy, see generate_scoring_report.py (this
page's numbers are the same ones, condensed -- re-verify against
bbb_scraper/match/merge.py's _INTEL block if that file changes).

Needs the `reports` extra: pip install -e ".[reports]"

Usage:
    python scripts/generate_scoring_onepager.py
    python scripts/generate_scoring_onepager.py --out data/processed/scoring_onepager.pdf
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Matches site/css/style.css's --accent -- same brand color as the other two
# scoring PDFs, not a separate report-only palette.
ACCENT = (23, 97, 74)
TEXT = (27, 27, 25)
MUTED = (108, 108, 102)
BOX_BG = (243, 246, 244)
PEAK_BG = (222, 232, 227)


class OnePager(FPDF):
    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "", 7.5)
        self.set_text_color(*MUTED)
        self.cell(0, 8,
                  "LossLess -- Lead Priority Score reference card. Source: bbb_scraper/match/merge.py (_INTEL). "
                  "Full walkthrough: scripts/generate_scoring_report.py",
                  align="C")

    def h2(self, text):
        self.set_font("Helvetica", "B", 11.5)
        self.set_text_color(*ACCENT)
        self.cell(0, 6, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(*ACCENT)
        self.set_line_width(0.5)
        y = self.get_y() + 0.5
        self.line(self.l_margin, y, self.l_margin + 22, y)
        self.ln(2.2)

    def p(self, text, size=8, color=TEXT, gap=1.5):
        self.set_font("Helvetica", "", size)
        self.set_text_color(*color)
        self.multi_cell(0, 4, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(gap)

    def table(self, headers, rows, widths, row_h=4.6, peak_col=None, font=7.6):
        self.set_font("Helvetica", "B", font)
        self.set_fill_color(*ACCENT)
        self.set_text_color(255, 255, 255)
        for h, w in zip(headers, widths):
            self.cell(w, row_h + 0.6, h, fill=True, border=0)
        self.ln()
        self.set_font("Helvetica", "", font)
        fill = False
        for row in rows:
            is_peak = peak_col is not None and "peak" in str(row[peak_col]).lower()
            bg = PEAK_BG if is_peak else ((*BOX_BG,) if fill else (255, 255, 255))
            self.set_fill_color(*bg)
            self.set_text_color(*TEXT)
            for cell, w in zip(row, widths):
                self.cell(w, row_h, str(cell), fill=True, border=0)
            self.ln()
            fill = not fill

    def bullet_line(self, weight, text, size=8):
        self.set_font("Helvetica", "B", size)
        self.set_text_color(*ACCENT)
        w_label = 13
        self.cell(w_label, 4.6, weight)
        self.set_font("Helvetica", "", size)
        self.set_text_color(*TEXT)
        self.set_x(self.l_margin + w_label)
        self.multi_cell(0, 4.6, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)


BASE_SIGNAL_ROWS = [
    ["Yelp / Angi / BBB-avg rating", "<= 1.5 stars", "45"],
    ["Yelp / Angi / BBB-avg rating", "> 1.5 - < 3.7 stars", "82 (peak)"],
    ["Yelp / Angi / BBB-avg rating", "3.7 - 4.19 stars", "34"],
    ["Yelp / Angi / BBB-avg rating", ">= 4.2 stars", "8"],
    ["Yelp / Angi review volume", "0 reviews", "40"],
    ["Yelp / Angi review volume", "1 - 60 reviews", "72 (peak)"],
    ["Yelp / Angi review volume", "61 - 150 reviews", "40"],
    ["Yelp / Angi review volume", "151+ reviews", "12"],
    ["BBB letter grade", "B- to C- (1.67 - 3.33)", "65 (peak)"],
    ["BBB letter grade", "below C- (< 1.67)", "32"],
    ["BBB letter grade", "B and above (> 3.33)", "22"],
    ["BBB complaints (total)", "0", "30"],
    ["BBB complaints (total)", "1 - 10", "68 (peak)"],
    ["BBB complaints (total)", "11 - 25", "50"],
    ["BBB complaints (total)", "26+", "25"],
    ["Review sentiment (local AI)", "no negative/mixed found", "18"],
    ["Review sentiment (local AI)", "<= 60% negative, most recent <= 6mo", "89.7 (peak)"],
    ["Review sentiment (local AI)", "60 - 99% negative, most recent <= 6mo", "69.0"],
    ["Review sentiment (local AI)", "100% negative, most recent <= 6mo", "51.7"],
]

BONUS_ROWS = [
    ("+12", "Reputation divergence -- BBB grade A- or better, but Yelp/Angi rating < 3.0, OR BBB review avg < 2.5, OR 5+ BBB complaints, OR most analyzed reviews read negative/mixed."),
    ("+8", "Accredited but low-rated -- BBB-accredited, yet Yelp, Angi, or BBB review average is under 3.0."),
    ("+6", "Gone quiet -- reviews have gone notably quiet vs. this business's OWN normal cadence (relative outlier, not a fixed day count)."),
    ("+6", "Established 10+ years -- enough history/revenue to plausibly afford the pitch."),
    ("+4", "BBB accredited -- already pays for one credibility product, a soft signal they'd consider another."),
]

REACH_ROWS = [
    ("x0.5", "No phone number on file -- the running total (base + bonuses) is CUT IN HALF."),
    ("+0", "Phone number present, no named contact -- no change."),
    ("+5", "Phone number AND a named BBB contact on file -- the call lands on a real person, not a front desk."),
]


def build(pdf: OnePager):
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 19)
    pdf.set_text_color(*ACCENT)
    pdf.cell(0, 9, "Lead Priority Score -- Metrics & Weights", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*MUTED)
    pdf.cell(0, 5, "0-130. Average of base signals (0-100 each) + flat bonuses, then scaled by reachability. v1, hand-tuned -- not a fitted model.",
              new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(3)

    pdf.h2("Step 1 -- Base signals (averaged, whichever are present)")
    pdf.table(
        ["Signal", "Condition", "Points"],
        BASE_SIGNAL_ROWS,
        [70, 82, 34],
        peak_col=2,
    )
    pdf.set_font("Helvetica", "I", 7.3)
    pdf.set_text_color(*MUTED)
    pdf.ln(1.3)
    pdf.multi_cell(0, 3.6,
        "Shaded rows = the peak of each signal's \"salvageable middle\" curve. Both extremes (already great / beyond help) score "
        "lower than a real, fixable, visible problem -- on purpose. A business with none of these signals gets no score at all "
        "(not a 0) and is excluded from ranking.",
        new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2.5)

    pdf.h2("Step 2 -- Bonus points (added on top of the averaged base)")
    for weight, text in BONUS_ROWS:
        pdf.bullet_line(weight, text)
    pdf.ln(1.5)

    pdf.h2("Step 3 -- Reachability scaling (applied last)")
    for weight, text in REACH_ROWS:
        pdf.bullet_line(weight, text)
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_text_color(*TEXT)
    pdf.set_fill_color(*BOX_BG)
    pdf.multi_cell(0, 5,
        "Final = min(130, (avg of base signals + bonuses) x reachability), rounded to 1 decimal. Can exceed 100 by design -- "
        "bonuses stack on top of a 0-100 base so a great lead is visibly distinguishable from a merely-good one.",
        fill=True, padding=3, new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "processed" / "scoring_onepager.pdf")
    args = ap.parse_args()

    pdf = OnePager()
    pdf.set_auto_page_break(auto=True, margin=14)
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

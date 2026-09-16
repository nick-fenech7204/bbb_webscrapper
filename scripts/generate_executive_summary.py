#!/usr/bin/env python
"""
One-page executive summary of the lead-scoring model -- plain-language, for
a stakeholder who wants "what does this do and why" in under a minute, not
the full formula walkthrough (that's scripts/generate_scoring_report.py).

Needs the `reports` extra: pip install -e ".[reports]"

Usage:
    python scripts/generate_executive_summary.py
    python scripts/generate_executive_summary.py --out data/processed/exec_summary.pdf
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Matches site/css/style.css's --accent exactly -- same brand color, not a
# separate report-only palette.
ACCENT = (23, 97, 74)
TEXT = (27, 27, 25)
MUTED = (108, 108, 102)
BOX_BG = (243, 246, 244)
PEAK_BG = (23, 97, 74)


class Summary(FPDF):
    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 10, "LossLess -- Lead Scoring, Executive Summary", align="C")

    def p(self, text, size=10, color=TEXT, gap=2.2):
        self.set_font("Helvetica", "", size)
        self.set_text_color(*color)
        self.multi_cell(0, 5.3, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(gap)

    def h2(self, text):
        self.ln(1)
        self.set_font("Helvetica", "B", 12.5)
        self.set_text_color(*ACCENT)
        self.multi_cell(0, 6.5, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(1)

    def bullet(self, label, text, size=9.5):
        self.set_font("Helvetica", "B", size)
        self.set_text_color(*ACCENT)
        x0 = self.get_x()
        w_label = self.get_string_width(label + "  ") + 2
        self.cell(w_label, 5, label)
        self.set_x(x0 + w_label)
        self.set_font("Helvetica", "", size)
        self.set_text_color(*TEXT)
        self.multi_cell(0, 5, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(0.8)


def build(pdf: Summary):
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(*ACCENT)
    pdf.cell(0, 11, "Lead Scoring: Executive Summary", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 10.5)
    pdf.set_text_color(*MUTED)
    pdf.cell(0, 6, "How LossLess decides which businesses are worth calling",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(5)

    pdf.h2("One number: Lead Priority Score (0-130)")
    pdf.p(
        "Every business gets a single score. It doesn't answer \"is this a good business\" -- "
        "it answers \"is this a good target for a reputation-management pitch, right now.\" "
        "Higher means a stronger, more actionable opportunity."
    )

    pdf.h2("The core idea: the salvageable middle")
    pdf.p(
        "A business that's already excellent scores LOW -- nothing to sell them. A business "
        "that's already a disaster also scores LOW -- probably a lost cause, or already "
        "working with someone. The highest scores go to businesses stuck in the middle: a "
        "real, visible, FIXABLE reputation problem, at a business established enough to "
        "actually pay for help."
    )
    bar_y = pdf.get_y()
    bar_h = 14
    bar_w = pdf.w - pdf.l_margin - pdf.r_margin
    seg_w = bar_w / 4
    segs = [("Beyond help", 0.35, False), ("Sweet spot", 1.0, True),
            ("Doing fine", 0.55, False), ("Already great", 0.15, False)]
    for i, (label, height_frac, peak) in enumerate(segs):
        x = pdf.l_margin + i * seg_w
        seg_h = bar_h * height_frac
        pdf.set_fill_color(*(PEAK_BG if peak else (198, 210, 205)))
        pdf.rect(x + 2, bar_y + (bar_h - seg_h), seg_w - 4, seg_h, style="F")
    pdf.set_y(bar_y + bar_h + 2)
    pdf.set_font("Helvetica", "", 7.6)
    pdf.set_text_color(*MUTED)
    for i, (label, _, peak) in enumerate(segs):
        pdf.set_x(pdf.l_margin + i * seg_w)
        pdf.cell(seg_w, 4, label, align="C")
    pdf.ln(9)

    pdf.h2("Three signal groups, blended")
    pdf.bullet("BBB", "grade, review average, complaint history")
    pdf.bullet("Yelp & Angi", "star rating and review volume, treated as equivalent signals")
    pdf.bullet("Review sentiment", "a local AI model reads the actual review TEXT (not just the "
               "star rating), and weighs how RECENT the negativity is -- a live problem counts "
               "more than an old one")
    pdf.ln(1)

    pdf.h2("On top of the fit score")
    pdf.p(
        "Targeted bonuses for the clearest pitches: a BBB page that looks clean but whose real "
        "reviews say otherwise; a business already paying for BBB accreditation despite being "
        "low-rated; one that's gone quiet. Then the whole score is scaled by reachability -- no "
        "phone on file cuts it in half, because the best-fit lead in the world is dead weight "
        "if nobody can call it."
    )

    pdf.h2("What changed this week")
    pdf.p(
        "Found and fixed a bug that was silently inflating scores for businesses with complaint "
        "history. Retired a second, separately-scaled \"reputation score\" that asked reps to "
        "reconcile two numbers instead of acting on one. Re-validated the publish-worthy cutoff "
        "against 13,000+ real scored businesses: precision jumps from ~11% to 81%+ right at a "
        "score of 50 -- that's the new bar, which is why lists going forward are dramatically "
        "shorter and higher-precision than before."
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "processed" / "exec_summary.pdf")
    args = ap.parse_args()

    pdf = Summary()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(18, 16, 18)
    build(pdf)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(args.out))
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

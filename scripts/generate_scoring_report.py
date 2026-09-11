#!/usr/bin/env python
"""
Generate a PDF explaining the lead-scoring model in bbb_scraper/match/merge.py:
what each derived score means, the exact formula behind it, and a real worked
example pulled live off a published dataset (not a hypothetical -- consistent
with this project's "real data over invented examples" habit).

This is a reference document for Nick (and whoever he shares it with, e.g.
his mentor) to understand exactly how the scoring works -- it is NOT part of
the deployed site and isn't regenerated automatically. Re-run it by hand
whenever the _INTEL formulas in merge.py change, so the doc doesn't go stale.

Needs the `reports` extra: pip install -e ".[reports]"

Usage:
    python scripts/generate_scoring_report.py
    python scripts/generate_scoring_report.py --out data/processed/scoring_model.pdf
    python scripts/generate_scoring_report.py --dataset car-dealers--miami-fl --example "Bird Road Auto Sales"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from bbb_scraper.match.normalize import letter_grade_to_num

SITE_DATA_DIR = REPO_ROOT / "site" / "data"

ACCENT = (23, 97, 74)     # matches site/css/style.css --accent
TEXT = (27, 27, 25)
MUTED = (108, 108, 102)
BOX_BG = (243, 246, 244)
FLAG_BG = (251, 240, 223)
FLAG_TX = (151, 89, 10)


# --------------------------------------------------------------------------
# Document
# --------------------------------------------------------------------------

class Report(FPDF):
    def multi_cell(self, w, h=None, text="", **kwargs):
        # fpdf2's own default leaves the cursor at the cell's right edge
        # (matching cell()'s behavior) rather than wrapping to a new line
        # at the left margin -- every use of multi_cell in this document is
        # "one paragraph, then move down", so make that the default here
        # instead of repeating these two kwargs at every call site.
        kwargs.setdefault("new_x", XPos.LMARGIN)
        kwargs.setdefault("new_y", YPos.NEXT)
        return super().multi_cell(w, h, text, **kwargs)

    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 8, "Lead Scoring Model", align="L")
        self.set_x(-40)
        self.cell(30, 8, "LossLess", align="R")
        self.ln(12)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")

    # ---- content helpers ----
    def h1(self, text):
        self.set_font("Helvetica", "B", 20)
        self.set_text_color(*ACCENT)
        self.multi_cell(0, 9, text)
        self.ln(2)

    def h2(self, text):
        self.ln(4)
        self.set_font("Helvetica", "B", 13)
        self.set_text_color(*TEXT)
        self.multi_cell(0, 7, text)
        self.set_draw_color(*ACCENT)
        self.set_line_width(0.6)
        y = self.get_y() + 1
        self.line(self.l_margin, y, self.l_margin + 28, y)
        self.ln(4)

    def h3(self, text):
        self.ln(2)
        self.set_font("Helvetica", "B", 10.5)
        self.set_text_color(*TEXT)
        self.multi_cell(0, 6, text)
        self.ln(1)

    def p(self, text, size=9.5, color=TEXT):
        self.set_font("Helvetica", "", size)
        self.set_text_color(*color)
        self.multi_cell(0, 5.2, text)
        self.ln(1.5)

    def bullet(self, text, indent=4):
        self.set_font("Helvetica", "", 9.5)
        self.set_text_color(*TEXT)
        x0 = self.get_x()
        self.set_x(x0 + indent)
        self.cell(4, 5.2, "-")
        self.set_x(x0 + indent + 4)
        self.multi_cell(0, 5.2, text)
        self.ln(0.5)

    def note(self, text):
        """Shaded callout box -- caveats, 'why', design-intent asides."""
        self.set_font("Helvetica", "I", 9)
        self.set_text_color(*MUTED)
        self.set_fill_color(*BOX_BG)
        x0 = self.get_x()
        w = self.w - self.l_margin - self.r_margin
        self.multi_cell(w, 5, text, fill=True, padding=3)
        self.set_xy(x0, self.get_y() + 2)

    def flag_chip(self, text):
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(*FLAG_TX)
        self.set_fill_color(*FLAG_BG)
        tw = self.get_string_width(text) + 5
        self.cell(tw, 6, text, fill=True, align="C")
        self.set_text_color(*TEXT)

    def table(self, headers, rows, widths, small=False):
        self.set_font("Helvetica", "B", 8.5 if not small else 8)
        self.set_fill_color(*ACCENT)
        self.set_text_color(255, 255, 255)
        for h, w in zip(headers, widths):
            self.cell(w, 7, h, fill=True, border=0)
        self.ln()
        self.set_font("Helvetica", "", 8.5 if not small else 8)
        self.set_text_color(*TEXT)
        fill = False
        for row in rows:
            self.set_fill_color(*BOX_BG) if fill else self.set_fill_color(255, 255, 255)
            for cell, w in zip(row, widths):
                self.cell(w, 6.5, str(cell), fill=True, border=0)
            self.ln()
            fill = not fill

    def spacer(self, h=3):
        self.ln(h)


# --------------------------------------------------------------------------
# Worked example: pull a real record straight off a published dataset
# --------------------------------------------------------------------------

def load_example(dataset_id: str, business_name: str) -> dict:
    path = SITE_DATA_DIR / f"{dataset_id}.json"
    if not path.exists():
        raise SystemExit(f"No such published dataset: {path}")
    records = json.loads(path.read_text(encoding="utf-8"))
    for r in records:
        if r.get("name", "").strip().lower() == business_name.strip().lower():
            return r
    raise SystemExit(f"{business_name!r} not found in {path.name}")


def trace_lead_priority(r: dict) -> list[tuple[str, str]]:
    """Recompute lead_priority_score step by step against the SAME logic as
    merge._lead_priority_score, returning (label, detail) rows for the PDF
    table -- so the worked example is a real, checkable trace, not prose
    that could quietly drift from the actual formula."""
    steps = []
    signals = []

    yr = r.get("yelp_rating") if r.get("on_yelp") and (r.get("yelp_review_count") or 0) >= 5 else None
    if yr is not None:
        v = 45 if yr <= 1.5 else 82 if yr < 3.7 else 34 if yr < 4.2 else 8
        signals.append(v)
        steps.append((f"Yelp rating signal ({yr} stars, {r.get('yelp_review_count')} reviews)", f"{v}/100"))

    yn = r.get("yelp_review_count")
    if r.get("on_yelp") and yn is not None:
        v = 40 if yn == 0 else 72 if yn <= 60 else 40 if yn <= 150 else 12
        signals.append(v)
        steps.append((f"Yelp volume signal ({yn} reviews)", f"{v}/100"))

    g = letter_grade_to_num(r.get("rating"))
    if g is not None:
        v = 65 if 1.67 <= g <= 3.33 else 32 if g < 1.67 else 22
        signals.append(v)
        steps.append((f"BBB grade signal ({r.get('rating')} = {g}/4.33)", f"{v}/100"))

    bavg = r.get("bbb_review_avg")
    if bavg is not None:
        v = 72 if bavg < 3.5 else 15
        signals.append(v)
        steps.append((f"BBB review-average signal ({bavg}/5)", f"{v}/100"))

    base = sum(signals) / len(signals) if signals else 0
    steps.append((f"Base = average of {len(signals)} signal(s)", f"{base:.1f}"))

    score = base
    if r.get("reputation_divergence_flag"):
        score += 12
        steps.append(("+ Reputation divergence (clean BBB grade, weak real feedback)", "+12"))
    if r.get("accredited_but_low_rated"):
        score += 8
        steps.append(("+ Accredited but low-rated", "+8"))
    bcomp = r.get("bbb_complaints_total")
    if bcomp is not None and 1 <= bcomp <= 25:
        score += 8
        steps.append((f"+ Has {bcomp} BBB complaint(s), in the 1-25 'active, fixable' range", "+8"))
    yrs = r.get("years_in_business")
    try:
        yrs_n = float(yrs)
    except (TypeError, ValueError):
        yrs_n = 0
    if yrs_n >= 10:
        score += 6
        steps.append((f"+ Established {int(yrs_n)}+ years", "+6"))
    if str(r.get("accredited")).strip().lower() in {"true", "1", "yes", "y", "t"}:
        score += 4
        steps.append(("+ BBB accredited", "+4"))

    has_phone = bool(str(r.get("phone") or "").strip())
    has_contact = bool(str(r.get("principal_contact") or "").strip())
    if has_phone:
        if has_contact:
            score += 5
            steps.append(("+ Reachable: phone + named contact", "+5"))
        else:
            steps.append(("Reachable: phone only", "+0"))
    else:
        score *= 0.5
        steps.append(("No phone at all -> reachability cut", "×0.5"))

    final = round(min(score, 130), 1)
    steps.append(("Final (capped at 130)", f"{final}"))
    return steps


# --------------------------------------------------------------------------
# Build the document
# --------------------------------------------------------------------------

def build(pdf: Report, example: dict, dataset_label: str):
    # ---- Cover ----
    pdf.add_page()
    pdf.ln(30)
    pdf.set_font("Helvetica", "B", 26)
    pdf.set_text_color(*ACCENT)
    pdf.multi_cell(0, 12, "Lead Scoring Model")
    pdf.set_font("Helvetica", "", 13)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(0, 8, "How LossLess scores a BBB + Yelp record as a sales lead")
    pdf.ln(6)
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(*TEXT)
    pdf.multi_cell(0, 5.5,
        "This site is a lead list for firms that sell review / reputation-management "
        "services -- every score below answers a version of \"how good a target is this "
        "business,\" not \"how good is this business.\" Source: bbb_scraper/match/merge.py "
        "(the `_INTEL` block). Generated by scripts/generate_scoring_report.py; re-run it "
        "after any change to that file so this document doesn't go stale.")

    # ---- Overview / the "why over 100" question ----
    pdf.add_page()
    pdf.h1("Overview: two scores, two different scales")
    pdf.p(
        "There are two top-line numbers on the Intelligence view, and they are deliberately "
        "NOT on the same scale -- neither is a percentage."
    )
    pdf.table(
        ["Score", "Range", "Question it answers"],
        [
            ["Reputation Score", "0 - 100", "How weak is this business's public reputation?"],
            ["Lead Priority Score", "0 - 130", "How good a sales lead is this, right now, for us?"],
        ],
        [55, 25, 108],
    )
    pdf.spacer(4)
    pdf.h3("Why can Lead Priority go over 100?")
    pdf.p(
        "Because it was never built as a 0-100 scale to begin with. It starts as an average "
        "of up to four 0-100 \"is this a good opportunity\" signals (that part alone is 0-100) "
        "-- and then real, specific, positive signals each add flat bonus points on top: a "
        "business that looks clean on paper but has weak real reviews (+12), one that already "
        "pays for BBB accreditation despite being low-rated (+8), a business old enough to "
        "matter (+6), and so on. A merely-good lead only clears one or two of those; a great "
        "lead clears most of them, and the bonus points are how the model tells those apart. "
        "The ceiling was deliberately set at 130, not 100, precisely so bonus-stacking has room "
        "to mean something -- capping it at 100 would flatten every strong lead down to the "
        "same number and erase exactly the distinction the bonuses exist to draw."
    )
    pdf.note(
        "In short: 101.7 isn't \"101.7%\" of anything. It's 101.7 points on a 0-130 scale -- "
        "meaning a base opportunity score plus several real, stacking reasons this specific "
        "business is worth calling today."
    )

    # ---- Reputation score ----
    pdf.add_page()
    pdf.h1("Reputation Score (0-100)")
    pdf.p(
        "A blended read on how weak this business's PUBLIC reputation looks across whatever "
        "signals are actually available. Higher = weaker = more of a reason to reach out. "
        "It is a weighted mean, re-weighted over only the signals present for that record -- "
        "a business with no BBB review data isn't penalized for the gap, its available signals "
        "just carry the full weight instead."
    )
    pdf.table(
        ["Signal", "Weight", "Only counted when"],
        [
            ["BBB letter grade", "30%", "always (every BBB record has one)"],
            ["Yelp star rating", "25%", "matched to Yelp, >= 5 Yelp reviews"],
            ["BBB review average", "15%", "BBB detail scrape, >= 3 BBB reviews"],
            ["BBB complaint count", "15%", "BBB detail scrape"],
            ["Yelp review volume", "15%", "matched to Yelp"],
        ],
        [70, 25, 93],
    )
    pdf.spacer(3)
    pdf.p(
        "Each signal is first converted to a 0-1 \"weakness\" fraction (e.g. Yelp weakness = "
        "(5.0 - rating) / 5.0, so a 5-star listing contributes 0 and an unrated-but-matched "
        "listing isn't counted at all), then combined as a weighted average and scaled to 0-100. "
        "None only if there's no BBB grade AND no Yelp rating at all -- effectively never, since "
        "every BBB record has a grade."
    )

    # ---- Lead priority score ----
    pdf.add_page()
    pdf.h1("Lead Priority Score (0-130)")
    pdf.p(
        "The actual sales-targeting score. NOT raw reputation weakness -- reputation_score "
        "above answers \"how bad does this look,\" this one answers \"how good a lead is this "
        "for a firm that sells review/reputation-management services,\" which is a different "
        "question. It favors the salvageable middle: a visible, fixable problem at a business "
        "mature enough to pay. An already-perfect business and a beyond-help one both score "
        "LOWER than a business stuck in the middle with a real, fixable gap."
    )
    pdf.h3("Step 1 -- base opportunity signals (averaged)")
    pdf.table(
        ["Signal", "Condition", "Points"],
        [
            ["Yelp rating", "<= 1.5 stars", "45"],
            ["Yelp rating", "1.5 - 3.7 stars", "82  (peak)"],
            ["Yelp rating", "3.7 - 4.2 stars", "34"],
            ["Yelp rating", "4.2+ stars", "8"],
            ["Yelp volume", "0 reviews", "40"],
            ["Yelp volume", "1 - 60 reviews", "72  (peak)"],
            ["Yelp volume", "61 - 150 reviews", "40"],
            ["Yelp volume", "150+ reviews", "12"],
            ["BBB grade", "B- to C- (1.67-3.33)", "65  (peak)"],
            ["BBB grade", "below C- (< 1.67)", "32"],
            ["BBB grade", "B and above (> 3.33)", "22"],
            ["BBB review avg", "under 3.5 / 5", "72"],
            ["BBB review avg", "3.5+ / 5", "15"],
        ],
        [45, 85, 58],
        small=True,
    )
    pdf.spacer(2)
    pdf.note(
        "Notice the shape: the best score in every row is in the MIDDLE, not at either end. "
        "That's the \"salvageable middle\" by design -- a 1-star business with 400 reviews is "
        "probably already using an agency or is beyond a quick fix; a 5-star business doesn't "
        "need the pitch at all."
    )
    pdf.h3("Step 2 -- bonus points (added on top)")
    pdf.bullet("+12  Reputation divergence: BBB grade looks clean (A- or better) but Yelp rating "
               "is under 3.0, OR BBB's own review average is under 2.5, OR there are 5+ BBB "
               "complaints. The core \"your BBB page looks great but here's the real story\" pitch.")
    pdf.bullet("+8  Accredited but low-rated: pays for BBB accreditation, yet Yelp rating or "
               "BBB review average is still under 3.0 -- already demonstrated willingness to pay "
               "for credibility, evidently not enough on its own.")
    pdf.bullet("+8  Has 1-25 BBB complaints: a live, addressable problem -- not zero, not so many "
               "it reads as a lost cause.")
    pdf.bullet("+6  Established 10+ years: enough revenue and history to plausibly afford and "
               "value the service.")
    pdf.bullet("+4  BBB accredited (on its own): already pays for one reputation/credibility "
               "product, a soft signal they'd consider another.")
    pdf.h3("Step 3 -- reachability (new, 2026-09-11)")
    pdf.p(
        "A great-fit lead nobody can call isn't a working lead yet, so the result is scaled by "
        "whether there's actually a way to reach the business:"
    )
    pdf.bullet("No phone number at all: the running total is CUT IN HALF (×0.5). Still "
               "findable, just real extra work before it's a \"call it this afternoon\" lead.")
    pdf.bullet("Phone number present, no named contact: no change.")
    pdf.bullet("Phone number AND a named BBB contact (e.g. \"Ray Saylor, President\"): +5. The "
               "call lands on a real person instead of a front desk.")
    pdf.spacer(2)
    pdf.p("The running total is then capped at 130 and rounded to one decimal place. That's the whole formula.")

    # ---- Contact readiness ----
    pdf.add_page()
    pdf.h1("Contact Readiness")
    pdf.p(
        "A separate, plain-language read on reachability alone -- shown as its own \"Reach\" "
        "column/badge so it's checkable independently of the fit score above, not just folded "
        "invisibly into it."
    )
    pdf.table(
        ["Has phone?", "Has named contact?", "Label", "Score"],
        [
            ["yes", "yes", "Phone + named contact", "100"],
            ["yes", "no", "Phone only", "75"],
            ["no", "contact or email", "Contact/email only, no phone", "30"],
            ["no", "no", "No direct contact info", "0"],
        ],
        [30, 45, 78, 20],
        small=True,
    )

    # ---- Flags ----
    pdf.add_page()
    pdf.h1("Flags")
    pdf.p("Boolean callouts shown as badges on the Intelligence table -- the same conditions that drive the bonus points above, surfaced individually:")
    pdf.spacer(1)
    pdf.flag_chip("Reputation gap")
    pdf.ln(9)
    pdf.p("BBB grade A- or better, but Yelp rating under 3.0, OR BBB review average under 2.5, OR 5+ BBB complaints.")
    pdf.flag_chip("Accredited, low-rated")
    pdf.ln(9)
    pdf.p("BBB-accredited, but Yelp rating or BBB review average under 3.0.")
    pdf.flag_chip("Few reviews")
    pdf.ln(9)
    pdf.p("Matched to Yelp with under 25 reviews -- thin volume, a review-growth pitch on its own even without a rating problem.")

    # ---- Worked example ----
    pdf.add_page()
    pdf.h1(f"Worked example: {example['name']}")
    pdf.p(f"A real record from the published {dataset_label} dataset -- not a hypothetical. "
          f"{example['city']}, {example['state']}.")
    pdf.table(
        ["Field", "Value"],
        [
            ["BBB grade", str(example.get("rating"))],
            ["Accredited", str(example.get("accredited"))],
            ["Years in business", str(example.get("years_in_business"))],
            ["BBB complaints", str(example.get("bbb_complaints_total"))],
            ["Yelp rating / reviews", f"{example.get('yelp_rating')} stars / {example.get('yelp_review_count')} reviews"],
            ["Phone", str(example.get("phone"))],
            ["Principal contact", str(example.get("principal_contact") or "(none on file)")],
        ],
        [55, 133],
        small=True,
    )
    pdf.spacer(3)
    pdf.h3("Lead Priority Score, traced step by step")
    steps = trace_lead_priority(example)
    pdf.table(["Step", "Value"], steps, [148, 40], small=True)
    pdf.spacer(3)
    pdf.note(
        f"Reputation score for this record: {example.get('reputation_score')}/100. Contact "
        f"readiness: \"{example.get('contact_readiness')}\" ({example.get('contact_readiness_score')}/100). "
        "This is exactly the model's target profile: an established, accredited business that "
        "looks fine on paper but has a real, visible gap in its actual customer reviews, and is "
        "fully reachable today."
    )

    # ---- Design philosophy / caveats ----
    pdf.add_page()
    pdf.h1("Design philosophy and caveats")
    pdf.bullet("This is a sales-targeting score, not a business-quality score. A 5-star, "
               "beloved business scores LOW here on purpose -- it isn't a lead for a "
               "reputation-management firm, it doesn't need one.")
    pdf.bullet("It favors the salvageable middle deliberately: both a business that's already "
               "fine and one that's far beyond help (extremely low-rated with a huge complaint "
               "volume, already probably working with someone or a lost cause) score lower than "
               "one with a real, fixable, visible problem.")
    pdf.bullet("Every weight and threshold in this document is v1 and hand-tuned off judgment, "
               "not a fitted model -- the code comments say so explicitly, and it's meant to be "
               "revisited after the metrics conversation with Nick's mentor, not treated as final.")
    pdf.bullet("Every derived column is computed defensively: if a formula raises for any reason "
               "on a given record, that one column comes back blank for that record rather than "
               "breaking the table for everyone else.")
    pdf.bullet("Publishing from an already-built master-table CSV recomputes every score fresh "
               "against the current formulas (merge.recompute_intel) rather than trusting "
               "whatever was baked into that CSV when it was written -- so a formula change here "
               "always reaches every published dataset the next time it's republished.")
    pdf.spacer(3)
    pdf.p("Full source: bbb_scraper/match/merge.py, the `_INTEL` dict and the functions it points to.", color=MUTED)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "processed" / "scoring_model.pdf")
    ap.add_argument("--dataset", default="plumbers--chicago-il", help="published dataset id for the worked example")
    ap.add_argument("--example", default="Larry's Plumbing", help="business name (must exist in --dataset)")
    args = ap.parse_args()

    example = load_example(args.dataset, args.example)
    dataset_label = args.dataset.replace("--", " / ")

    pdf = Report()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(18, 15, 18)
    build(pdf, example, dataset_label)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(args.out))
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

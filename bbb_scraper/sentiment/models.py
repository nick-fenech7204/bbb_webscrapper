"""Data shapes for local review-sentiment analysis."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ReviewSentiment:
    """One review's analysis result -- one entry per review that had
    analyzable text, JSON-in-cell in the review_sentiment column (same
    convention as mapquest_reviews/angi_reviews/bbb_reviews).

    `date` carries through whatever precision the source review actually
    had -- an exact "YYYY-MM-DD" for bbb/mapquest, "YYYY-MM-01" (a
    same-month approximation, never a guessed day) for angi, whose own
    date_label is only ever a month+year like "April 2026". None when the
    source date couldn't be parsed at all.
    """

    source: str
    """"bbb" | "angi" | "mapquest" -- which review-capture pipeline this came from."""
    text_hash: str
    """First 16 hex chars of sha256(review text) -- lets a later run recognize
    "already analyzed this exact review" and skip re-paying Ollama's ~2-4s/call
    without re-storing the full text a second time (it's already sitting in
    mapquest_reviews/angi_reviews/bbb_reviews). Not a security hash, just a
    cheap dedupe key."""
    date: str | None
    rating: float | None
    sentiment: str | None
    """"positive" | "negative" | "mixed" | "neutral" -- the model's own call, not derived from `rating`."""
    severity: int | None
    """1-5, how reputation-damaging this specific review reads -- only meaningful
    for negative/mixed sentiment; the model still returns something for positive/
    neutral reviews (typically 1) rather than leaving the field out, so it's
    always present when analysis succeeded at all."""
    theme: str | None
    """Short free-text category the model chose, e.g. "quality of work",
    "communication", "pricing" -- not a fixed enum; see client.py's prompt."""
    actionable_for_pitch: bool | None
    """Would a reputation-management pitch referencing this specific complaint
    make sense? The model's own judgment call, not derived from severity alone
    (a severe but already-resolved-sounding complaint might read as not
    actionable; a minor but still-live one might)."""
    summary: str | None

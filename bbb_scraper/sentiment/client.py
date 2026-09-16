"""
Local review-sentiment analysis via Ollama -- zero-shot prompting, no
training/fine-tuning.

**Confirmed live, 2026-09-15, against a real captured review.** Ollama's
own /api/generate endpoint, `format: "json"` (forces valid JSON back, not
just "please respond in JSON" in the prompt text alone -- this actually
matters, confirmed: without it the model sometimes wraps its answer in
prose) plus a prompt describing the exact output shape wanted. A real
1-star home-inspection review came back
{"sentiment": "negative", "severity": 5, "theme": "quality of work",
"actionable_for_pitch": true, "summary": "..."} in 4.4s. This is NOT
training or fine-tuning -- llama3.2 already has this skill from its own
training; prompting just directs it at this specific task. Fine-tuning
would be for teaching a genuinely new skill/style from labeled examples,
which sentiment/theme classification isn't.

**Local, not a hosted API.** A loopback call to Nick's own machine
(default http://localhost:11434) -- no API key, no proxy (nothing to
evade, no rate limit to respect, no per-request cost), no rate limiting
of our own either. The real constraint here is latency, not politeness:
~44s cold (model load) then ~2-4s/call once warm, regardless of review
length (confirmed live) -- the timeout has to cover the worst case.

**Never fatal.** Same contract as bbb_scraper.webcheck.check_website:
Ollama not running, a timeout, a malformed response -- caught, logged,
returns None for that one review, never raises. A whole business's
sentiment analysis is best-effort the same way MapQuest/Angi matching is;
a run with Ollama down just means empty review_sentiment columns, never a
crashed batch.
"""
from __future__ import annotations

import json

from curl_cffi import requests as curl_requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from bbb_scraper.config import Settings
from bbb_scraper.config import settings as default_settings
from bbb_scraper.logging_setup import get_logger

logger = get_logger(__name__)

_VALID_SENTIMENTS = {"positive", "negative", "mixed", "neutral"}

# Every field here is what actually came back from the real 2026-09-15 test
# call (see module docstring) -- "theme" is deliberately free-text, not a
# fixed enum, since a 3B model asked to pick from a rigid closed list
# tends to force-fit whatever's closest rather than saying "other".
_PROMPT_TEMPLATE = """You are analyzing a customer review of a local service business for a reputation-management sales tool. Respond with ONLY valid JSON, no other text, matching this exact shape:
{{"sentiment": "positive" | "negative" | "mixed" | "neutral", "severity": 1-5 (1=minor gripe or none, 5=serious/reputation-damaging), "theme": a short category like "quality of work", "communication", "pricing", "professionalism", "missed issue", "scheduling", "other", "actionable_for_pitch": true or false (would a reputation-management pitch referencing this specific complaint make sense?), "summary": a plain one-sentence summary}}

Review{rating_note}:
"{text}\""""


class OllamaClient:
    def __init__(self, cfg: Settings | None = None):
        self.cfg = cfg or default_settings
        self.session = curl_requests.Session()
        self.calls_made = 0  # real Ollama calls this client instance made (cache hits in analyze.py don't count)

    def analyze_review(self, text: str, *, rating: float | None = None) -> dict | None:
        """One review's text -> a raw dict with sentiment/severity/theme/
        actionable_for_pitch/summary keys, or None if the text was empty
        or analysis failed for any reason. Never raises -- see module
        docstring."""
        text = (text or "").strip()
        if not text:
            return None

        rating_note = f" (rating given: {rating}/5 stars)" if rating is not None else ""
        prompt = _PROMPT_TEMPLATE.format(rating_note=rating_note, text=text)

        @retry(reraise=True, stop=stop_after_attempt(self.cfg.ollama_max_retries),
               wait=wait_exponential(multiplier=1.0, min=1, max=10),
               retry=retry_if_exception_type(curl_requests.exceptions.RequestException))
        def _do_request():
            response = self.session.post(
                f"{self.cfg.ollama_base_url}/api/generate",
                json={"model": self.cfg.ollama_model, "prompt": prompt, "stream": False, "format": "json"},
                timeout=self.cfg.ollama_timeout_seconds,
            )
            self.calls_made += 1
            response.raise_for_status()
            return response

        try:
            response = _do_request()
        except Exception:
            logger.exception("Ollama request failed -- is it running at %s?", self.cfg.ollama_base_url)
            return None

        try:
            raw = json.loads(response.json()["response"])
        except (KeyError, ValueError, TypeError):
            logger.warning("Ollama returned something that wasn't the expected JSON shape: %r",
                            response.text[:200])
            return None

        return _validate(raw)

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> OllamaClient:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def is_available(cfg: Settings | None = None) -> bool:
    """Cheap health check -- hits /api/tags (lists installed models,
    doesn't load one) rather than a real /api/generate call, so checking
    "is Ollama even reachable" doesn't pay the ~44s cold-start cost just
    to find out. Used once per batch (see scripts/batch_scrape_metros.py's
    _open_sentiment) to decide whether to enable this step at all --
    same "resolve once, best-effort" shape as the Yelp-key/Angi-category
    checks."""
    cfg = cfg or default_settings
    try:
        response = curl_requests.get(f"{cfg.ollama_base_url}/api/tags", timeout=5.0)
        response.raise_for_status()
        return True
    except Exception:  # noqa: BLE001 -- a health check that itself can fail defeats the point
        return False


def _validate(raw: dict) -> dict | None:
    """Defensive against a model that mostly-but-not-quite follows the
    schema (a real risk with a 3B model, not paranoia) -- coerces what it
    reasonably can, but drops the result entirely if sentiment itself
    isn't one of the four real values, since that field is load-bearing
    for every downstream signal; a bad/missing theme or summary is only
    cosmetic and gets None instead."""
    if not isinstance(raw, dict):
        return None
    sentiment = raw.get("sentiment")
    if sentiment not in _VALID_SENTIMENTS:
        return None

    severity = raw.get("severity")
    try:
        severity = max(1, min(5, int(severity)))
    except (TypeError, ValueError):
        severity = None

    actionable = raw.get("actionable_for_pitch")
    if not isinstance(actionable, bool):
        actionable = None

    return {
        "sentiment": sentiment,
        "severity": severity,
        "theme": str(raw["theme"]) if raw.get("theme") else None,
        "actionable_for_pitch": actionable,
        "summary": str(raw["summary"]) if raw.get("summary") else None,
    }

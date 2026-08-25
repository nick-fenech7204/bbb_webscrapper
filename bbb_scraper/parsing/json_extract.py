"""
Generic helpers for pulling embedded JSON out of HTML.

These are schema-agnostic on purpose -- they don't know anything about BBB's
actual field names. `search_parser.py` / `business_parser.py` use these to
get raw dicts, then do the BBB-specific field mapping.

Two shapes covered, matching what you described:
  1. `<script type="application/json">...</script>` tags (common at listing
     level) -- extracted with `extract_json_scripts` / `extract_script_json_by_id`.
  2. A `window.__SOMETHING__ = {...};` assignment inside a plain `<script>`
     tag (common at the individual page level, e.g. a "preloaded state")
     -- extracted with `extract_window_assignment`.

For (2) we deliberately don't use a naive regex like "= curly-brace .* curly-brace ;"
scanning the whole document -- BBB's state blobs are large and nested, and `.*` (even
DOTALL) will either stop at the first `};` it finds inside a nested
object/string, or over-match into the next statement. We also only search
inside actual `<script>` tag contents (not the full HTML text), since the
assignment text could otherwise coincidentally appear in a comment or
visible page copy. Once the assignment is located, a small brace-depth scan
(string/escape aware) finds the exact matching closing brace.
"""
from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup

from bbb_scraper.exceptions import ParsingError
from bbb_scraper.logging_setup import get_logger

logger = get_logger(__name__)


def extract_json_scripts(html: str, content_type: str = "application/json") -> list[dict[str, Any]]:
    """Parse every `<script type="{content_type}">` tag as JSON.

    Silently skips tags that fail to parse (logged as warnings) -- a listing
    page often has several unrelated JSON scripts (analytics config, schema.org
    markup, etc.), so a single bad one shouldn't blow up the whole page.
    """
    soup = BeautifulSoup(html, "lxml")
    results: list[dict[str, Any]] = []
    for tag in soup.find_all("script", attrs={"type": content_type}):
        text = tag.string or tag.get_text()
        if not text or not text.strip():
            continue
        try:
            results.append(json.loads(text))
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse %s script block as JSON: %s", content_type, exc)
    return results


def extract_script_json_by_id(html: str, script_id: str) -> dict[str, Any] | None:
    soup = BeautifulSoup(html, "lxml")
    tag = soup.find("script", id=script_id)
    if tag is None:
        return None
    text = tag.string or tag.get_text()
    if not text or not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParsingError(f"script#{script_id} did not contain valid JSON: {exc}") from exc


def extract_next_data(html: str) -> dict[str, Any] | None:
    """Shortcut for Next.js's `<script id="__NEXT_DATA__" type="application/json">`."""
    return extract_script_json_by_id(html, "__NEXT_DATA__")


def extract_window_assignment(html: str, var_name: str) -> dict[str, Any] | None:
    """Find `window.{var_name} = { ... };` (or `var {var_name} = {...}`) inside
    a <script> block's contents and return the parsed JSON object.

    Only searches within actual <script> tag text (not the raw HTML string),
    so an incidental mention of `window.{var_name} =` in an HTML comment or
    on-page copy can't produce a false match. Returns None if the variable
    isn't found in any script tag. Raises ParsingError if it's found but the
    braces don't balance / JSON doesn't parse.
    """
    pattern = re.compile(r"(?:window\.)?" + re.escape(var_name) + r"\s*=\s*")
    soup = BeautifulSoup(html, "lxml")

    for tag in soup.find_all("script"):
        text = tag.string or tag.get_text()
        if not text:
            continue
        match = pattern.search(text)
        if not match:
            continue

        brace_start = text.find("{", match.end())
        if brace_start == -1:
            raise ParsingError(f"Found `{var_name} =` but no opening brace followed it")

        obj_text = _extract_balanced_json_object(text, brace_start)
        try:
            return json.loads(obj_text)
        except json.JSONDecodeError as exc:
            raise ParsingError(f"Extracted `{var_name}` text was not valid JSON: {exc}") from exc

    return None


def _extract_balanced_json_object(text: str, start: int) -> str:
    """Return the substring of `text` starting at `start` (which must point at
    an opening `{`) through its matching closing `}`, respecting string
    literals and escape sequences so braces inside strings don't confuse the
    depth count.
    """
    if text[start] != "{":
        raise ParsingError("_extract_balanced_json_object: start index is not '{'")

    depth = 0
    in_string = False
    escape = False

    for i in range(start, len(text)):
        ch = text[i]

        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    raise ParsingError("Unbalanced braces: reached end of document before matching '}' found")

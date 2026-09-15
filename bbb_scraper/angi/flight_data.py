"""Reassemble Next.js "flight" (React Server Components) push chunks into
one plain-text blob.

Angi's pages ship their real data as a series of
`<script>self.__next_f.push([1,"<escaped text>"])</script>` calls -- each
argument is a JSON string literal (so `\\"` -> `"`, `\\n` -> a real
newline, etc.) that, once decoded and concatenated in document order,
reconstructs one big text stream of `<chunk-id>:<value>\\n`-separated
entries (React's flight protocol; the numbered/hex ids are cross-references
between chunks -- `"results":"$90"` means "see the chunk labeled 90").

Confirmed necessary by testing, not assumed: searching the *raw* HTML
directly for a field like `resultCount` works on some pages and silently
fails on others, because the escaping depth isn't constant -- a small
city's page had single backslash-escaping (`\\"resultCount\\":19`), a
bigger city's had what looked like double (`\\\\"resultCount\\\\":1091`,
though it's really the same single escaping -- Python's `repr()` of a
backslash prints as two characters, which read wrong at a glance the first
time). Reassembling once up front means every downstream parser only ever
sees one plain, unescaped representation, regardless of a given page's
chunking/escaping quirks.
"""
from __future__ import annotations

import json
import re

_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,(".*?")\]\)', re.DOTALL)


def reassemble(html: str) -> str:
    """Concatenate every push chunk's decoded string, in document order.

    Each chunk is a JSON string literal in the HTML source -- decoding with
    `json.loads` (not a hand-rolled unescape) handles every JSON escape
    correctly (`\\"`, `\\n`, `\\uXXXX`, ...), not just the couple this
    project happened to hit by hand. A chunk that somehow isn't valid JSON
    (shouldn't happen -- the source generated it -- but a malformed/partial
    response is possible) is skipped rather than raising, since this is a
    best-effort reconstruction: losing one chunk's text just means whatever
    it held isn't found later, the same "missing, not corrupted" failure
    mode as a field that was never present in the first place.
    """
    parts = []
    for match in _PUSH_RE.finditer(html):
        try:
            parts.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            continue
    return "".join(parts)

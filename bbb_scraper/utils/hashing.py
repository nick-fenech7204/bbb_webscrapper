"""Small hashing helpers used for raw-capture filenames and stable IDs."""
from __future__ import annotations

import hashlib


def sha256_hex(text: str, length: int | None = 16) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return digest[:length] if length else digest

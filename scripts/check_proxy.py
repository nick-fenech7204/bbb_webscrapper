#!/usr/bin/env python
"""
Verify the proxy configured in .env actually works, before pointing it at
BBB. Requests an IP-check endpoint through the proxy and prints the result
-- if it's working, the returned IP should be the proxy's, not your own.

    python scripts/check_proxy.py
    python scripts/check_proxy.py --url https://ip.decodo.com/json

No credentials are read from anywhere but your local .env -- see
PROXY_HOST / PROXY_PORT / PROXY_USERNAME / PROXY_PASSWORD in .env.example.
"""
from __future__ import annotations

import argparse
import sys

from bbb_scraper.logging_setup import configure_logging, get_logger
from bbb_scraper.scraping.client import HttpClient

logger = get_logger(__name__)

DEFAULT_CHECK_URL = "https://ip.decodo.com/json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Sanity-check the configured proxy")
    parser.add_argument(
        "--url", default=DEFAULT_CHECK_URL, help="IP-check endpoint to request through the proxy"
    )
    args = parser.parse_args()

    configure_logging()

    with HttpClient() as http:
        response = http.get(args.url)

    print(response.text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

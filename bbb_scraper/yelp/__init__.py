"""Yelp Fusion API integration.

Deliberately separate from `bbb_scraper.scraping` -- that package scrapes
bbb.org (Cloudflare, needs curl_cffi impersonation + a residential proxy).
This talks to Yelp's *official* API (api.yelp.com, bearer-token auth).
Scraping yelp.com itself is a dead end: it's DataDome-protected and 403s a
fully-impersonated request on the first hit (confirmed 2026-09-10), and the
page's bootstrap JSON withholds organic business data anyway.
"""

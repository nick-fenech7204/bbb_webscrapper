"""Checks whether a business's own listed website is actually live --
a separate sales signal from the BBB/Yelp reputation scoring ("we can also
sell you a website" alongside "we can sell you reputation management").
See checker.py / enrich.py for the split (single-URL check vs. batch
enrichment with caching + concurrency)."""

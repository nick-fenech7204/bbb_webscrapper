"""BBB <-> Yelp record linkage.

Deliberately does NOT match on street address (normalization headache,
suite-line noise, PO boxes, "Blanding Blvd" coded to three different
cities) -- confirmed not worth it for this dataset. The location signal
comes from ZIP, city/state, and straight-line distance between the two
sources' own coordinates instead.

Pipeline: normalize -> block (shared phone / shared ZIP / <3mi) -> score
candidate pairs on weighted signals -> greedy 1:1 assignment -> master
table (bbb_* | yelp_* columns) + a v1 derived-intelligence column set.
"""

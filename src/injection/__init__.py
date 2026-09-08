"""Injection layer: Brain intent -> Tuzzina Public API payload.

This package translates a Brain-owned publishing decision
(CanonicalIntent) into the exact payload Tuzzina's POST /posts
expects, using the platform adapter for shaping and TuzzinaClient
for transport. It performs NO HTTP itself, runs NO scheduler,
manages NO tokens, stores NOTHING.
"""

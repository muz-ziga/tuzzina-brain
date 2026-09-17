"""Discovery core (Stage C). Pure source-polling policy: which
configured sources are due for a poll right now. No network,
no clock, no state, no LLM.

Separation (non-negotiable): source polling cadence lives
here; publication schedule lives in G3; research lead time
lives in the Slot contract (g3.planner). The three never
share a field. The scheduler (Stage 9) calls due_sources()
with its own now and polls only what is due; a quiet source
produces no candidates, which produces no LLM calls.
"""

"""Tuzzina-backed StateStore: durable monitoring state with rows
owned by Tuzzina (Postgres) and domain owned by Brain.

The store speaks only two narrow endpoints (GET+PUT
/public/v1/research-state/:sourceId, API-key auth, org resolved
server-side). It stores the SourceState dict verbatim and
rebuilds SourceState defensively (missing/wrong-typed fields
fall back to defaults; unknown keys are dropped, never trusted).

No secrets, no content beyond the monitoring cursor, no
publishing state. A missing row (HTTP 404) loads as fresh
state; any other failure raises so the caller never mistakes
an outage for "nothing seen".
"""
from __future__ import annotations

from research.monitor import SourceState, StateStore
from tuzzina.client import TuzzinaClient, TuzzinaError

_STATE_KEYS = ("source_id", "last_seen_item_id",
               "last_seen_published_at", "seen", "first_seen_at",
               "last_checked_at", "last_error")


def state_to_dict(state: SourceState) -> dict:
    return {
        "source_id": state.source_id or "",
        "last_seen_item_id": state.last_seen_item_id or "",
        "last_seen_published_at": state.last_seen_published_at or "",
        "seen": dict(state.seen) if isinstance(state.seen, dict)
        else {},
        "first_seen_at": state.first_seen_at or "",
        "last_checked_at": state.last_checked_at or "",
        "last_error": state.last_error or "",
    }


def state_from_dict(source_id: str, data) -> SourceState:
    """Rebuild state defensively: wrong types fall back, unknown
    keys are dropped."""
    if not isinstance(data, dict):
        return SourceState(source_id=source_id)
    seen = data.get("seen")
    return SourceState(
        source_id=source_id,
        last_seen_item_id=str(data.get("last_seen_item_id") or ""),
        last_seen_published_at=str(
            data.get("last_seen_published_at") or ""),
        seen=dict(seen) if isinstance(seen, dict) else {},
        first_seen_at=str(data.get("first_seen_at") or ""),
        last_checked_at=str(data.get("last_checked_at") or ""),
        last_error=str(data.get("last_error") or ""),
    )


class TuzzinaStateStore(StateStore):
    """Durable StateStore over Tuzzina rows. One store instance
    serves every source (the server scopes rows per organization
    from the API key). No local caching: every load/save is one
    HTTP call, so concurrent runners observe each other's
    commits (last-write-wins on collision, same as any shared
    row — no distributed locks)."""

    def __init__(self, client: TuzzinaClient):
        if client is None:
            raise TuzzinaError("TuzzinaClient is required")
        self._client = client

    def load(self, source_id: str) -> SourceState:
        data = self._client.get_research_state(source_id)
        return state_from_dict(source_id, data)

    def save(self, state: SourceState) -> None:
        if not state or not state.source_id:
            raise TuzzinaError("cannot save state without source_id")
        self._client.save_research_state(state.source_id,
                                         state_to_dict(state))

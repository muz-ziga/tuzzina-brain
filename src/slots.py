"""Per-publication-slot executor (Stage H). One slot in,
one independent execution context out — never a campaign
batch, never LLM, never publication.

A slot owns NOTHING until the atomic claim succeeds:
select -> claim -> context. Every other outcome is a
terminal, deterministic, non-owning status (no-op,
expired, invalid, disabled, claim-lost, claim-failed).

Pure orchestration over injected seams (pool dict,
claim/release callables, explicit ``now``): no network, no
clock, no store, no models, no Tuzzina client import. The
production wiring (Tuzzina claim endpoint, pool
persistence) stays behind the injected callables, which
Stage I will supply; this module cannot tell test doubles
from production.

Idempotency comes from the claim primitive, not from
memory: repeating a slot with the SAME run_id re-wins its
own claim (server treats same-owner reclaim as success),
so the same context rebuilds instead of duplicating. A
different run_id loses explicitly (claim-lost). No locks,
no Redis, no Temporal, no second scheduler.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone

STATUS_READY = "ready"
STATUS_NO_OP = "no-op"
STATUS_EXPIRED = "expired"
STATUS_INVALID = "invalid"
STATUS_DISABLED = "disabled"
STATUS_CLAIM_LOST = "claim-lost"
STATUS_CLAIM_FAILED = "claim-failed"


@dataclass
class SlotExecution:
    """Handoff context for exactly one slot. Candidate provenance
    is carried verbatim as data (untrusted content is never
    interpreted, executed, or granted authority by this
    module)."""
    slot_id: str = ""
    campaign_id: str = ""
    publish_at: str = ""
    research_start_at: str = ""
    deadline_at: str = ""
    candidate_id: str = ""
    run_id: str = ""
    candidate: dict = field(default_factory=dict)
    status: str = ""
    error: str = ""


def slot_id(campaign_id: str, publish_at: str) -> str:
    """Deterministic slot identity for logging, claim
    ownership, and replay detection. Pure string join; both
    parts stripped, never normalized beyond that."""
    return "%s@%s" % ((campaign_id or "").strip(),
                       (publish_at or "").strip())


def _parse_dt(value: str):
    try:
        text = (value or "").strip()
        if not text:
            return None
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _base_context(*, campaign_id: str, slot, sid: str,
                  status: str, error: str = "") -> SlotExecution:
    try:
        publish = str(getattr(slot, "publish_at", "") or "")
    except Exception:
        publish = ""
    try:
        start = str(getattr(slot, "research_start_at", "") or "")
    except Exception:
        start = ""
    try:
        deadline = str(getattr(slot, "deadline_at", "") or "")
    except Exception:
        deadline = ""
    return SlotExecution(
        slot_id=sid, campaign_id=campaign_id, publish_at=publish,
        research_start_at=start, deadline_at=deadline,
        status=status, error=error)


def execute_slot(*, campaign_id: str, slot, pool,
                 now: str, claim, release, run_id: str,
                 enabled: bool = True,
                 max_age_days=None) -> SlotExecution:
    """Execute one publication slot through selection and
    atomic claim. Returns a terminal SlotExecution in every
    case; never raises for domain outcomes (only for
    programming errors like a missing claim callable)."""
    if claim is None or release is None:
        raise ValueError("claim and release callables required")
    cid = (campaign_id or "").strip()
    try:
        publish_raw = str(getattr(slot, "publish_at", "") or "")
    except Exception:
        publish_raw = ""
    sid = slot_id(cid, publish_raw)
    if not cid or _parse_dt(publish_raw) is None:
        return _base_context(campaign_id=cid, slot=slot,
                             sid=sid, status=STATUS_INVALID,
                             error="invalid-slot")
    if enabled is False:
        return _base_context(campaign_id=cid, slot=slot,
                             sid=sid, status=STATUS_DISABLED,
                             error="slot-disabled")
    moment = _parse_dt(now)
    if moment is None:
        return _base_context(campaign_id=cid, slot=slot,
                             sid=sid, status=STATUS_INVALID,
                             error="invalid-now")
    try:
        deadline_raw = str(getattr(slot, "deadline_at", "") or "")
    except Exception:
        deadline_raw = ""
    deadline = _parse_dt(deadline_raw) or _parse_dt(publish_raw)
    if deadline is not None and moment > deadline:
        return _base_context(campaign_id=cid, slot=slot,
                             sid=sid, status=STATUS_EXPIRED,
                             error="past-deadline")
    from discovery.pool import select as pool_select
    try:
        options = pool_select(pool, now=now,
                              max_age_days=max_age_days, limit=1)
    except Exception as e:
        return _base_context(campaign_id=cid, slot=slot,
                             sid=sid, status=STATUS_INVALID,
                             error="%s: pool-select-failed" %
                             type(e).__name__)
    if not options:
        return _base_context(campaign_id=cid, slot=slot,
                             sid=sid, status=STATUS_NO_OP,
                             error="no-eligible-candidate")
    candidate = options[0] if isinstance(options[0], dict) \
        else {}
    candidate_id = candidate.get("candidate_id", "")
    if not isinstance(candidate_id, str) or not candidate_id:
        return _base_context(campaign_id=cid, slot=slot,
                             sid=sid, status=STATUS_NO_OP,
                             error="no-eligible-candidate")
    try:
        won = claim(candidate_id, run_id)
    except Exception as e:
        return _base_context(campaign_id=cid, slot=slot,
                             sid=sid, status=STATUS_CLAIM_FAILED,
                             error="%s: claim-transport-failed" %
                             type(e).__name__)
    if not won:
        return _base_context(campaign_id=cid, slot=slot,
                             sid=sid, status=STATUS_CLAIM_LOST,
                             error="claimed-by-other")
    ctx = _base_context(campaign_id=cid, slot=slot, sid=sid,
                        status=STATUS_READY)
    ctx.candidate_id = candidate_id
    ctx.run_id = run_id
    try:
        ctx.candidate = dict(candidate)
    except Exception:
        try:
            release(candidate_id, run_id)
        except Exception:
            pass
        return _base_context(campaign_id=cid, slot=slot,
                             sid=sid, status=STATUS_CLAIM_FAILED,
                             error="context-build-failed")
    return ctx

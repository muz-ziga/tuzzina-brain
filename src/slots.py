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


def materialize_slots(campaign_id: str, publish_ats: list,
                      lead_time_min: int = 30) -> list:
    """Deterministic slot materialization (Stage K, step 4).

    Pure: no network, no LLM, no pool, no clock.
    Same campaign + same publish_at list (in given order) →
    same logical Slot objects with identical
    slot_id=campaign@publish_at. Uses the approved
    g3.planner.research_window() for timing so
    research_start_at/deadline_at stay the single source
    of truth.
    """
    cid = (campaign_id or "").strip()
    if not cid:
        raise ValueError("campaign_id is required")
    if not isinstance(publish_ats, list):
        raise ValueError("publish_ats must be a list")
    from g3.planner import research_window as _window
    out = []
    for raw in publish_ats:
        publish = str(raw or "").strip()
        if not publish:
            raise ValueError("publish_at is required")
        window = _window(publish, lead_time_min=lead_time_min)
        sid = slot_id(cid, window.publish_at)
        out.append(SlotExecution(
            slot_id=sid, campaign_id=cid,
            publish_at=window.publish_at,
            research_start_at=window.research_start_at,
            deadline_at=window.deadline_at))
    return out


def is_due(slot, now: str) -> bool:
    """Due = now in [research_start_at, deadline_at].
    Uses existing execute_slot window semantics: deadline is
    slot.deadline_at or slot.publish_at, research_start is
    slot.research_start_at. Pure, deterministic, no side
    effects. Returns False for invalid inputs instead of
    raising (orchestrator treats it as not due).
    """
    try:
        start_raw = str(getattr(slot, "research_start_at", "") or "")
        deadline_raw = str(getattr(slot, "deadline_at", "") or "")
        publish_raw = str(getattr(slot, "publish_at", "") or "")
    except Exception:
        return False
    start = _parse_dt(start_raw)
    deadline = _parse_dt(deadline_raw) or _parse_dt(publish_raw)
    moment = _parse_dt(now)
    if start is None or deadline is None or moment is None:
        return False
    return start <= moment <= deadline


def run_campaign_slots(*, campaign_id: str, publish_ats: list,
                       pool, now: str, claim, release, client,
                       run_id_prefix: str = "run",
                       max_age_days=None,
                       get_publication=None,
                       integration_id: str = "cmtr8cxod0003qh788fwdt2jq",
                       mode: str = "draft") -> list:
    """Campaign orchestration over already-materialized slots.
    Pure orchestration: for each due slot, reuses
    execute_slot -> run_slot_content -> slot_publish.
    No scheduler, no queue, no new DB. Terminal slots are
    skipped via get_publication (ledger) when supplied;
    otherwise per-slot execute_slot handles claim-lost.
    Failure of one slot never stops others. Discovery is
    never forced (pool consumed as-is).
    Returns list of per-slot terminal results (SlotExecution
    or SlotContent or SlotPublication), in materialized order.
    """
    slots = materialize_slots(campaign_id, publish_ats)
    results = []
    for idx, slot in enumerate(slots):
        # 1. due check
        if not is_due(slot, now):
            # preserve deterministic expired/no-op via execute_slot
            # semantics: run execute_slot to get correct status
            # (expired vs not-yet-due as no-op). But is_due false
            # before research_start should be treated as not due
            # (no execution), not expired. Match Stage K spec:
            # before research_start → not due (no execution).
            start = _parse_dt(slot.research_start_at)
            moment = _parse_dt(now)
            if start is not None and moment is not None and moment < start:
                results.append(SlotExecution(
                    slot_id=slot.slot_id, campaign_id=campaign_id,
                    publish_at=slot.publish_at,
                    research_start_at=slot.research_start_at,
                    deadline_at=slot.deadline_at,
                    status="not-due", error="not-yet-due"))
                continue
            # after deadline → expired (use execute path for correct code)
            results.append(SlotExecution(
                slot_id=slot.slot_id, campaign_id=campaign_id,
                publish_at=slot.publish_at,
                research_start_at=slot.research_start_at,
                deadline_at=slot.deadline_at,
                status=STATUS_EXPIRED, error="past-deadline"))
            continue
        # 2. terminal check via ledger when supplied
        if get_publication is not None:
            try:
                # ledger key is (slot_id, intent_id) where intent_id
                # is slot_id + ":0" for the first intent of the slot
                probe_intent = "%s:0" % slot.slot_id
                existing = get_publication(slot.slot_id, probe_intent)
                if isinstance(existing, dict):
                    st = str(existing.get("status") or "")
                    if st in ("published", "failed"):
                        results.append(SlotExecution(
                            slot_id=slot.slot_id,
                            campaign_id=campaign_id,
                            publish_at=slot.publish_at,
                            research_start_at=slot.research_start_at,
                            deadline_at=slot.deadline_at,
                            status="already-complete",
                            error="terminal:%s" % st))
                        continue
            except Exception:
                pass
        # 3. per-slot H (candidate select+claim)
        run_id = "%s-%d" % ((run_id_prefix or "run"), idx)
        exec_ctx = execute_slot(
            campaign_id=campaign_id, slot=slot, pool=pool, now=now,
            claim=claim, release=release, run_id=run_id,
            max_age_days=max_age_days)
        if exec_ctx.status != STATUS_READY:
            results.append(exec_ctx)
            continue
        # 4. I (research→generation) — only if H succeeded
        try:
            from slot_content import run_slot_content as _run_content
            content = _run_content(
                exec_ctx, integration_id=integration_id,
                mode=mode, release=release)
        except Exception as e:
            results.append(SlotExecution(
                slot_id=slot.slot_id, campaign_id=campaign_id,
                publish_at=slot.publish_at,
                research_start_at=slot.research_start_at,
                deadline_at=slot.deadline_at,
                status="content-failed",
                error="%s: %s" % (type(e).__name__, str(e)[:120])))
            continue
        if getattr(content, "status", "") != "ready":
            results.append(content)
            continue
        # 5. J (publication) — only if I succeeded
        try:
            from slot_publish import execute_publication as _publish
            pubs = _publish(
                content, publish_at=slot.publish_at,
                client=client, release=release)
            # pubs is list per intent; extend results with them
            results.extend(pubs if isinstance(pubs, list) else [pubs])
        except Exception as e:
            results.append(SlotExecution(
                slot_id=slot.slot_id, campaign_id=campaign_id,
                publish_at=slot.publish_at,
                research_start_at=slot.research_start_at,
                deadline_at=slot.deadline_at,
                status="publication-failed",
                error="%s: %s" % (type(e).__name__, str(e)[:120])))
    return results


def window_publish_ats(schedule: dict, window_start: str,
                       window_end: str, limit: int = 100) -> list:
    """Bounded recurring-window derivation (Stage K-4).

    Pure: schedule dict + window bounds in → finite ordered
    publish_ats out. No network, no claim, no pool, no clock
    beyond the supplied bounds. Uses g3.planner.next_slots as
    single source of slot times so Slot/research_window stay
    consistent. Caller supplies the window; finite campaigns
    pass their horizon, recurring campaigns pass the current
    invocation window (next invocation supplies the next
    window). Deterministic: same inputs → same outputs.
    """
    if not isinstance(schedule, dict):
        raise ValueError("schedule must be a mapping")
    if limit is None or not isinstance(limit, int) or limit < 1 or limit > 500:
        raise ValueError("limit must be 1..500")
    start_dt = _parse_dt(window_start)
    end_dt = _parse_dt(window_end)
    if start_dt is None or end_dt is None:
        raise ValueError("window bounds must be ISO datetime")
    if end_dt < start_dt:
        raise ValueError("window_end before window_start")
    from g3.planner import next_slots as _next
    # generate from just before window_start so the first slot
    # at window_start is included; filter to window inclusive
    from datetime import timedelta
    probe = start_dt - timedelta(seconds=1)
    raw = _next(schedule.get("days") or [],
                schedule.get("times") or [],
                schedule.get("timezone") or "UTC",
                limit, now=probe,
                start_time=str(schedule.get("start_time") or ""),
                end_time=str(schedule.get("end_time") or ""),
                spacing_minutes=schedule.get("spacing_minutes"))
    out = []
    for iso in raw:
        dt = _parse_dt(iso)
        if dt is None:
            continue
        if start_dt <= dt <= end_dt:
            out.append(iso)
        if dt > end_dt:
            break
    return out


def run_campaign(*, campaign_id: str, publish_ats: list,
                 pool, now: str, claim, release, client,
                 get_publication=None, max_age_days=None,
                 run_id_prefix: str = "run",
                 integration_id: str = "cmtr8cxod0003qh788fwdt2jq",
                 mode: str = "draft") -> list:
    """Bounded campaign worker (Stage K-3).

    Thin orchestration over run_campaign_slots: the caller
    supplies the bounded horizon as publish_ats (finite list).
    For recurring campaigns the caller supplies the next
    window; for finite campaigns the whole horizon. No
    scheduler, no clock, no DB beyond the ledger probe.
    """
    return run_campaign_slots(
        campaign_id=campaign_id, publish_ats=publish_ats, pool=pool,
        now=now, claim=claim, release=release, client=client,
        get_publication=get_publication, max_age_days=max_age_days,
        run_id_prefix=run_id_prefix, integration_id=integration_id,
        mode=mode)
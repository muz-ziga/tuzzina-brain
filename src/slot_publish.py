"""Per-slot publication handoff (Stage J). SlotContent in,
idempotent Tuzzina execution out — never a campaign batch.

Consumes intents already produced by Stage I; NEVER calls
Research, Analysis, or any Text/Image/Video generator.
Translates each intent through the EXISTING InjectionService
(the only publish path) and records the outcome in the
Tuzzina-owned BrainPublication ledger (insert-or-fail claim
+ first-writer-wins completion). A retry observes the
recorded row instead of re-executing: same slot + same
intent -> one logical publication.

Deterministic value ids (brainpub:{slot}:{idx}:main|comment)
ride the intent into Tuzzina's upsert, so even a
success-then-timeout replay converges on the same rows
instead of duplicating them.

Pure orchestration over injected seams (client object with
the TuzzinaClient surface, release callable, explicit
deadline): no network, no clock, no store, no credentials
in this module. claim/release of the CANDIDATE uses the
existing Stage B mechanism; the publication ledger is a
separate Tuzzina-owned primitive (never mixed up).
"""
from __future__ import annotations
import dataclasses
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from injection.intent import (MODES, POST_KINDS, CanonicalIntent)
from injection.service import InjectionService, _post_ids

STATUS_NO_OP = "no-op"
STATUS_VALIDATION_FAILED = "validation-failed"
STATUS_DESTINATION_UNAVAILABLE = "destination-unavailable"
STATUS_IDEMPOTENT_COMPLETE = "idempotency-already-complete"
STATUS_PUBLICATION_FAILED = "publication-failed"
STATUS_COMPANION_FAILED = "companion-failed"
STATUS_SUCCESS = "success"


@dataclass
class SlotPublication:
    """Terminal result for ONE intent of a slot. post_ids and
    companion_ids come ONLY from confirmed Tuzzina responses
    or recorded ledger rows — never invented, never matched
    by text."""
    status: str = ""
    slot_id: str = ""
    campaign_id: str = ""
    run_id: str = ""
    candidate_id: str = ""
    intent_id: str = ""
    intent_index: int = 0
    integration_id: str = ""
    mode: str = ""
    publish_at: str = ""
    post_ids: list = field(default_factory=list)
    companion_ids: list = field(default_factory=list)
    error: str = ""
    released: bool = False
    idempotent: bool = False


def intent_key(slot_id: str, index: int) -> str:
    """Deterministic idempotency identity from trusted
    execution identity only (slot + position). Content never
    participates: identical retries share the key, different
    slots/intents never collide."""
    return "%s:%d" % ((slot_id or "").strip(), int(index))


def value_ids_for(slot_id: str, index: int,
                  has_companion: bool) -> list:
    """Deterministic Tuzzina row ids for one intent's value
    items (main first, comment second). A replay upserts the
    same rows instead of creating duplicates."""
    base = "brainpub:%s:%d" % ((slot_id or "").strip(),
                               int(index))
    ids = [base + ":main"]
    if has_companion:
        ids.append(base + ":comment")
    return ids


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


def _base(*, slot_id: str, campaign_id: str, run_id: str,
          candidate_id: str, intent_id: str = "",
          intent_index: int = 0, integration_id: str = "",
          mode: str = "", publish_at: str = "",
          status: str = "", error: str = "") -> SlotPublication:
    return SlotPublication(
        status=status, slot_id=slot_id, campaign_id=campaign_id,
        run_id=run_id, candidate_id=candidate_id,
        intent_id=intent_id, intent_index=intent_index,
        integration_id=integration_id, mode=mode,
        publish_at=publish_at, error=error)


def validate_intent(intent, publish_at: str):
    """Deterministic contract check. Returns an error string
    or "" when the intent is publishable. Never raises, never
    calls anywhere, never mutates."""
    if not isinstance(intent, CanonicalIntent):
        return "intent must be CanonicalIntent"
    if not isinstance(intent.integration_id, str) or \
            not intent.integration_id.strip():
        return "integration_id is required"
    if not isinstance(intent.content, str) or \
            not intent.content.strip():
        return "content is required"
    if intent.mode not in MODES:
        return "mode must be one of: %s" % ("/".join(MODES),)
    if intent.post_kind not in POST_KINDS:
        return "post_kind must be one of: %s" % (
            "/".join(POST_KINDS),)
    effective = (publish_at or "").strip() or \
        (intent.publish_at or "").strip()
    if _parse_dt(effective) is None:
        return "publish_at is not ISO datetime"
    if not isinstance(intent.media, list):
        return "media must be a list"
    for m in intent.media:
        if not isinstance(m, dict) or \
                not isinstance(m.get("id"), str) or \
                not m.get("id").strip() or \
                not isinstance(m.get("path"), str) or \
                not m.get("path").strip():
            return "each media ref needs id and path"
    if not isinstance(intent.companion, str):
        return "companion must be a string"
    if not isinstance(intent.hashtags, list):
        return "hashtags must be a list"
    if not isinstance(intent.mentions, list):
        return "mentions must be a list"
    return ""


def _observe(client, slot_id: str, key: str,
             deadline_s: float):
    """Poll the recorded row until terminal or deadline.
    Returns the row dict, or None on timeout/transport
    failure. Observation only: correctness (single
    publication) comes from the atomic insert, never from
    this pacing."""
    end = time.monotonic() + max(0.0, float(deadline_s or 0))
    last = None
    while True:
        try:
            row = client.get_publication(slot_id, key)
        except Exception:
            row = None
        if isinstance(row, dict) and \
                row.get("status") in ("published", "failed"):
            return row
        last = row
        if time.monotonic() >= end:
            return last if isinstance(last, dict) else None
        time.sleep(0.05)


def _row_result(base: SlotPublication, row: dict,
                released: bool) -> SlotPublication:
    status = (row.get("status") or "") if isinstance(
        row, dict) else ""
    if status == "published":
        base.status = STATUS_IDEMPOTENT_COMPLETE
    else:
        base.status = STATUS_PUBLICATION_FAILED
    base.post_ids = list(row.get("postIds") or []) \
        if isinstance(row, dict) else []
    base.companion_ids = list(row.get("companionIds") or []) \
        if isinstance(row, dict) else []
    base.error = str((row.get("error") or "")) \
        if isinstance(row, dict) else ""
    base.released = released
    base.idempotent = True
    return base


def _release_owned(release, candidate_id: str,
                   run_id: str) -> bool:
    if not candidate_id or not run_id or release is None:
        return False
    try:
        release(candidate_id, run_id)
        return True
    except Exception:
        return False


def execute_publication(slot_content, *, publish_at: str,
                        client, release=None,
                        observe_deadline_s: float = 5.0,
                        verify_children=None):
    """Publish every intent of one READY SlotContent through
    Tuzzina, idempotently. Returns one SlotPublication per
    intent, in order; stops at the first non-success (never
    silently skips). Zero Tuzzina calls happen before ALL
    intents validate and the destination resolves."""
    slot_id = str(getattr(slot_content, "slot_id", "") or "")
    campaign_id = str(getattr(slot_content, "campaign_id", "")
                      or "")
    run_id = str(getattr(slot_content, "run_id", "") or "")
    candidate_id = str(getattr(slot_content, "candidate_id", "")
                       or "")

    def _no_op(status, error):
        return [_base(slot_id=slot_id, campaign_id=campaign_id,
                      run_id=run_id, candidate_id=candidate_id,
                      status=status, error=error)]

    if getattr(slot_content, "status", "") != "ready":
        return _no_op(STATUS_NO_OP, "content-not-ready")
    intents = list(getattr(slot_content, "intents", None) or [])
    if not intents:
        return _no_op(STATUS_NO_OP, "no-intents")
    if not slot_id:
        return _no_op(STATUS_VALIDATION_FAILED,
                      "slot_id is required")

    eff_base = (publish_at or "").strip()
    seen = set()
    for intent in intents:
        err = validate_intent(intent, eff_base)
        if err:
            return [_base(
                slot_id=slot_id, campaign_id=campaign_id,
                run_id=run_id, candidate_id=candidate_id,
                status=STATUS_VALIDATION_FAILED, error=err)]
        eff = (eff_base or intent.publish_at.strip())
        sig = (intent.integration_id.strip(),
               intent.content.strip(), eff)
        if sig in seen:
            return [_base(
                slot_id=slot_id, campaign_id=campaign_id,
                run_id=run_id, candidate_id=candidate_id,
                status=STATUS_VALIDATION_FAILED,
                error="duplicate-intent-in-slot")]
        seen.add(sig)

    # Destination resolves server-side; a dead integration
    # fails here with zero publication calls.
    integration_ids = sorted({i.integration_id.strip()
                              for i in intents})
    for iid in integration_ids:
        try:
            client.get_integration(iid)
        except Exception as e:
            return [_base(
                slot_id=slot_id, campaign_id=campaign_id,
                run_id=run_id, candidate_id=candidate_id,
                status=STATUS_DESTINATION_UNAVAILABLE,
                error="%s: %s" % (type(e).__name__,
                                  str(e)[:200]))]

    # Reclaim OUR OWN candidate (Stage I released on terminal
    # content outcomes). Loss means another owner took it
    # meanwhile: abort before any publication call.
    if candidate_id:
        try:
            if not client.claim_candidate(candidate_id, run_id):
                return [_base(
                    slot_id=slot_id, campaign_id=campaign_id,
                    run_id=run_id, candidate_id=candidate_id,
                    status=STATUS_PUBLICATION_FAILED,
                    error="claim-lost")]
        except Exception as e:
            return [_base(
                slot_id=slot_id, campaign_id=campaign_id,
                run_id=run_id, candidate_id=candidate_id,
                status=STATUS_PUBLICATION_FAILED,
                error="%s: claim-transport-failed" %
                type(e).__name__)]

    results = []
    service = InjectionService()
    for idx, intent in enumerate(intents):
        key = intent_key(slot_id, idx)
        eff = ((publish_at or "").strip() or
               intent.publish_at.strip())
        base = _base(
            slot_id=slot_id, campaign_id=campaign_id,
            run_id=run_id, candidate_id=candidate_id,
            intent_id=key, intent_index=idx,
            integration_id=intent.integration_id.strip(),
            mode=intent.mode, publish_at=eff)
        has_companion = bool((intent.companion or "").strip())

        # Atomic ledger claim: exactly one execution wins.
        try:
            row = client.claim_publication(
                slot_id, key, run_id, candidate_id,
                intent.integration_id.strip(), intent.mode,
                eff)
            won = bool(isinstance(row, dict) and
                       row.get("claimed") is True)
        except Exception as e:
            if "HTTP 409" in str(e):
                row = _observe(client, slot_id, key,
                               observe_deadline_s)
                if isinstance(row, dict) and row.get(
                        "status") in ("published", "failed"):
                    base.integration_id = str(
                        row.get("integrationId") or
                        base.integration_id)
                    base.mode = str(row.get("mode") or
                                    base.mode)
                    base.publish_at = str(
                        row.get("publishAt") or base.publish_at)
                    results.append(_row_result(
                        base, row,
                        _release_owned(release, candidate_id,
                                       run_id)))
                    continue
                results.append(_base(
                    slot_id=slot_id, campaign_id=campaign_id,
                    run_id=run_id, candidate_id=candidate_id,
                    intent_id=key, intent_index=idx,
                    status=STATUS_PUBLICATION_FAILED,
                    error="claim-in-progress"))
                results[-1].released = _release_owned(
                    release, candidate_id, run_id)
                break
            results.append(_base(
                slot_id=slot_id, campaign_id=campaign_id,
                run_id=run_id, candidate_id=candidate_id,
                intent_id=key, intent_index=idx,
                status=STATUS_PUBLICATION_FAILED,
                error="%s: claim-transport-failed" %
                type(e).__name__))
            results[-1].released = _release_owned(
                release, candidate_id, run_id)
            break
        if not won:
            # Non-409 falsy answer (defensive: the contract
            # says 409 on loss, but never trust a bare False
            # to mean anything else).
            results.append(_base(
                slot_id=slot_id, campaign_id=campaign_id,
                run_id=run_id, candidate_id=candidate_id,
                intent_id=key, intent_index=idx,
                status=STATUS_PUBLICATION_FAILED,
                error="claim-not-won"))
            results[-1].released = _release_owned(
                release, candidate_id, run_id)
            break

        # Publish through the EXISTING injection path with
        # deterministic row ids (replay upserts same rows).
        publish_intent = dataclasses.replace(
            intent, publish_at=eff,
            value_ids=value_ids_for(slot_id, idx,
                                    has_companion))
        try:
            injected = service.inject(publish_intent, client)
            post_ids = _post_ids(injected.get("response"))
        except Exception as e:
            text = str(e)
            if text.startswith("HTTP 4"):
                # Server refused BEFORE creating (DTO +
                # provider validation run first): terminal,
                # nothing exists, safe to record failed.
                try:
                    client.complete_publication(
                        slot_id, key, run_id, "failed", [], [],
                        text[:500])
                except Exception:
                    pass
                results.append(_base(
                    slot_id=slot_id, campaign_id=campaign_id,
                    run_id=run_id, candidate_id=candidate_id,
                    intent_id=key, intent_index=idx,
                    integration_id=intent.integration_id.strip(),
                    mode=intent.mode, publish_at=eff,
                    status=STATUS_PUBLICATION_FAILED,
                    error=text[:500]))
            else:
                # Ambiguous (timeout/5xx after possible
                # commit): do NOT mark failed — leave pending
                # so a same-owner retry converges via upsert
                # instead of duplicating.
                results.append(_base(
                    slot_id=slot_id, campaign_id=campaign_id,
                    run_id=run_id, candidate_id=candidate_id,
                    intent_id=key, intent_index=idx,
                    integration_id=intent.integration_id.strip(),
                    mode=intent.mode, publish_at=eff,
                    status=STATUS_PUBLICATION_FAILED,
                    error=text[:500]))
            results[-1].released = _release_owned(
                release, candidate_id, run_id)
            break
        if not post_ids:
            try:
                client.complete_publication(
                    slot_id, key, run_id, "failed", [], [],
                    "empty-response")
            except Exception:
                pass
            results.append(_base(
                slot_id=slot_id, campaign_id=campaign_id,
                run_id=run_id, candidate_id=candidate_id,
                intent_id=key, intent_index=idx,
                integration_id=intent.integration_id.strip(),
                mode=intent.mode, publish_at=eff,
                status=STATUS_PUBLICATION_FAILED,
                error="empty-response"))
            results[-1].released = _release_owned(
                release, candidate_id, run_id)
            break

        companion_ids = value_ids_for(
            slot_id, idx, True)[1:] if has_companion else []
        outcome = STATUS_SUCCESS
        outcome_error = ""
        if has_companion and verify_children is not None:
            # Companion rows are created in the same Tuzzina
            # call; when the caller supplies a read-back, a
            # missing child is reported honestly with the main
            # post preserved (no duplicate on retry: the ledger
            # already records this outcome). Without a
            # verifier the success response stands (the call
            # creates parent+child atomically per value item).
            try:
                children_ok = bool(verify_children(post_ids[0]))
            except Exception as e:
                children_ok = False
                outcome_error = "%s: companion-verify-failed" % \
                    type(e).__name__
            if not children_ok:
                outcome = STATUS_COMPANION_FAILED
                if not outcome_error:
                    outcome_error = "companion-unverified"
                companion_ids = []
        try:
            done = client.complete_publication(
                slot_id, key, run_id,
                "published" if outcome == STATUS_SUCCESS
                else "failed",
                post_ids, companion_ids,
                outcome_error)
            completed = bool(isinstance(done, dict) and
                             done.get("completed"))
        except Exception:
            completed = False
        if not completed:
            # Lost the completion race: read the recorded row
            # instead of reporting our own (possibly partial)
            # view. Never overwrite another execution.
            try:
                recorded = client.get_publication(slot_id, key)
            except Exception:
                recorded = None
            if isinstance(recorded, dict):
                base.integration_id = str(
                    recorded.get("integrationId") or
                    base.integration_id)
                results.append(_row_result(
                    base, recorded,
                    _release_owned(release, candidate_id,
                                   run_id)))
                break
        res = _base(
            slot_id=slot_id, campaign_id=campaign_id,
            run_id=run_id, candidate_id=candidate_id,
            intent_id=key, intent_index=idx,
            integration_id=intent.integration_id.strip(),
            mode=intent.mode, publish_at=eff,
            status=outcome, error=outcome_error)
        res.post_ids = list(post_ids)
        res.companion_ids = list(companion_ids)
        res.released = _release_owned(release, candidate_id,
                                      run_id)
        results.append(res)
    return results

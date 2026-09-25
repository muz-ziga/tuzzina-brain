"""Frozen legacy semantic floors (contract id "legacy-v1").

These are the exact output floors the validators enforced before
semantic contracts existed: moved here verbatim, never edited.
A skill WITHOUT a contract block resolves to "legacy-v1", so
current behavior is byte-identical; the resolution is recorded
in run evidence (never a silent compatibility branch).

New skills declare their own contract (skill config.contract);
only the structural core in models.py/analysis.py applies to
them. Sunset of legacy-v1 is an explicit owner decision, not
an architectural default.
"""
from __future__ import annotations

CONTRACT_ID = "legacy-v1"


def research_floors(data: dict, known: set, vocab) -> None:
    """Raise vocab.Error unless the legacy research floors hold.
    vocab carries (FACT, INFERENCE, HIGH, MEDIUM, LOW,
    MAX_FINDINGS, Error) so this frozen module never imports the
    live models module (no cycle, no drift). Verbatim pre-split
    behavior."""
    fact, inference, high, medium, low, max_findings, error = vocab
    raw_findings = data.get("findings")
    for f in raw_findings[:max_findings]:
        if not isinstance(f, dict):
            raise error("invalid-output")
        kind = f.get("kind")
        conf = f.get("confidence")
        ids = f.get("item_ids")
        stmt = f.get("statement")
        if kind not in (fact, inference) or \
                conf not in (high, medium, low) or \
                not isinstance(ids, list) or not ids or \
                not all(isinstance(i, str) and i in known
                        for i in ids) or \
                not isinstance(stmt, str) or not stmt.strip():
            raise error("invalid-output")
    if not raw_findings:
        # An EMPTY result is a valid research result: the research
        # found nothing that met the skill's rules, and the analysis
        # stage's Skill decides what that means. The summary floor
        # below exists to describe findings, so requiring prose for
        # zero findings only turned a correct answer into a retry.
        return
    summary = data.get("summary", "")
    if not isinstance(summary, str) or not summary.strip():
        raise error("invalid-output")


def analysis_floors(data: dict) -> None:
    """Structural floor only: every fact the model returned must be a
    non-empty string.

    The former "an eligible verdict must carry at least one fact"
    rule is REMOVED. Whether a decision needs evidence, how much, and
    what counts as sufficient is the analysis SKILL's rule (the
    default skill states it in its own instructions); a central floor
    silently overrode every campaign's declared editorial policy.
    Shape (types, id membership) is checked by the caller and is not
    repeated here."""
    from research.analysis import AnalysisError
    facts = data.get("facts", [])
    if not all(isinstance(f, str) and f.strip() for f in facts):
        raise AnalysisError("invalid-output")

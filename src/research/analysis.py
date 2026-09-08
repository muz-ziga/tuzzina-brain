"""Analysis Model (R4): ResearchResult + Brain policy -> decision.

Role boundary: this layer answers "IS THERE A CONTENT OPPORTUNITY
AND WHAT SHOULD THE CONTENT DECISION BE?" It never writes final
captions, generates media, publishes, schedules, fetches, touches
monitoring state, or calls Tuzzina. Downstream (G2) owns final
text/policy transformation; this layer only hands it a validated
ContentOpportunity.

Contract notes:
- One analyze() call yields AT MOST one opportunity. Splitting
  unrelated topics across calls is the orchestrator's future job.
- Item-level NEW/UNCHANGED/UPDATED stays in research/monitor.py.
  Here, novelty means: this NEW research holds >=1 source-backed
  FACT (inference-only research is not an opportunity).
- `policy` is a plain Brain-vocabulary dict the caller assembles,
  e.g. {"brand": {...}, "language": {"default": "ar"},
  "pillars": [...], "generation": {...}, "media": {...}}.
  Pillars/generation live in campaign + ChannelStrategy; the
  resolved run cfg carries brand/language/media_policy. Only
  allowlisted policy keys are ever read (anything provider-shaped
  is ignored by construction, asserted in tests).
- Media intent: "none" | "image" | "video". Explicit
  policy media.prefer wins; else visual source material (item
  images, youtube origin) or a min_items>0 demand yields "image";
  otherwise "none". "video" is reachable only via explicit prefer
  (no heuristic invents video work).
"""
from __future__ import annotations
import json
import socket
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

ELIGIBLE_FORMATS = ("post",)
MEDIA_INTENTS = ("none", "image", "video")
PRIORITIES = ("low", "normal", "high")
CONFIDENCES = ("high", "medium", "low")

MAX_FINDINGS = 30
MAX_PROMPT_CHARS = 6000


class AnalysisError(Exception):
    """Analysis failure: empty/malformed research, invalid policy,
    model timeout or failure, invalid structured output, oversized
    context. Raising changes nothing (no state, no content, no
    Tuzzina, no strategy writes)."""


@dataclass
class ContentOpportunity:
    eligible: bool
    topic: str = ""
    angle: str = ""
    rationale: str = ""
    facts: list = field(default_factory=list)  # [str], FACT-kind only
    source_item_ids: list = field(default_factory=list)
    content_format: str = "post"
    media_intent: str = "none"
    audience: str = ""
    language: str = ""
    priority: str = "normal"
    confidence: str = "low"
    constraints: list = field(default_factory=list)  # G2 must-respect
    model: str = ""
    meta: dict = field(default_factory=dict)


class AnalysisModel(ABC):
    @abstractmethod
    def analyze(self, result, policy: dict,
                items: list | None = None) -> ContentOpportunity:
        """Decide opportunity from research + policy. `items` is
        optional source material used ONLY for media presence;
        decisions never fetch, store, or publish."""


def _tokens(s: str) -> set[str]:
    return {t.strip(".,!?:;()\"'").lower()
            for t in (s or "").split() if len(t.strip(".,!?:;()\"'")) > 3}


def _policy_view(policy) -> dict:
    if not isinstance(policy, dict):
        raise AnalysisError("invalid-policy")
    brand = policy.get("brand") or {}
    language = policy.get("language") or {}
    media = policy.get("media") or {}
    if not all(isinstance(x, dict) for x in (brand, language, media)):
        raise AnalysisError("invalid-policy")
    pillars = policy.get("pillars") or []
    if not isinstance(pillars, list) or \
            not all(isinstance(p, str) for p in pillars):
        raise AnalysisError("invalid-policy")
    return {
        "tone": str(brand.get("tone") or "neutral"),
        "audience": str(brand.get("audience") or "general"),
        "language": str(language.get("default") or "ar"),
        "pillars": [p.strip().lower() for p in pillars if p.strip()],
        "media": media,
    }


def _findings_of(result) -> list:
    findings = getattr(result, "findings", None)
    if not isinstance(findings, list):
        raise AnalysisError("malformed-research")
    for f in findings:
        if not hasattr(f, "statement") or not hasattr(f, "kind") or \
                not hasattr(f, "item_ids"):
            raise AnalysisError("malformed-research")
    return findings


def _pillar_match(pillars: list, result) -> str:
    if not pillars:
        return ""
    hay = set()
    for t in list(getattr(result, "topics", []) or []):
        hay |= _tokens(t)
    for f in _findings_of(result):
        hay |= _tokens(f.statement)
    for p in pillars:
        if p in hay or any(p in h or h in p for h in hay if h):
            return p
    return ""


def _media_intent(view: dict, items) -> str:
    prefer = str((view["media"] or {}).get("prefer") or "").lower()
    if prefer in ("image", "video"):
        return prefer
    visual = False
    for it in items or []:
        if getattr(it, "images", None):
            visual = True
            break
        if getattr(it, "source_type", "") in ("youtube",):
            visual = True
            break
    if visual:
        return "image"
    try:
        if int((view["media"] or {}).get("min_items") or 0) > 0:
            return "image"
    except Exception:
        pass
    return "none"


class MockAnalysisModel(AnalysisModel):
    """Deterministic decisions: eligible iff >=1 FACT finding and
    (no pillars configured or a pillar token matches research).
    Facts stay FACT-sourced; inference ids join references only."""

    def analyze(self, result, policy: dict,
                items: list | None = None) -> ContentOpportunity:
        if result is None or not isinstance(
                getattr(result, "findings", None), list):
            raise AnalysisError("malformed-research")
        view = _policy_view(policy)
        findings = _findings_of(result)
        if not findings:
            return self._out(False, "no-findings", view, [], [], result)
        facts = [f for f in findings if f.kind == "fact"]
        if not facts:
            return self._out(False, "no-source-facts", view, [], [],
                             result)
        pillar = _pillar_match(view["pillars"], result)
        if view["pillars"] and not pillar:
            return self._out(False, "pillar-mismatch", view, [], [],
                             result)
        return self._out(True, "evidence+policy-match", view, facts,
                         findings, result, items, pillar)

    def _out(self, eligible, reason, view, facts, findings, result,
             items=None, pillar=""):
        ids: list[str] = []
        for f in findings:
            for i in (f.item_ids or []):
                if i not in ids:
                    ids.append(i)
        topic = ""
        if eligible:
            topics = list(getattr(result, "topics", []) or [])
            topic = (topics[0] if topics else
                     (facts[0].statement if facts else ""))[:120]
        angle = ""
        if eligible:
            lead = facts[0].statement[:120] if facts else topic
            angle = (f"{pillar + ': ' if pillar else ''}{lead} "
                     f"({view['tone']} angle for {view['audience']})")
        media = _media_intent(view, items) if eligible else "none"
        constraints = []
        if eligible:
            constraints = [f"language={view['language']}",
                           f"audience={view['audience']}"]
            if pillar:
                constraints.append(f"pillar={pillar}")
        # Eligibility already enforces pillar fit, so confidence
        # is pure evidence strength: >=2 facts -> high, else medium.
        confidence = "high" if len(facts) >= 2 else "medium"
        return ContentOpportunity(
            eligible=eligible, topic=topic, angle=angle,
            rationale=(f"{reason}; {len(facts)} fact(s) over "
                       f"{len(ids)} source item(s)"),
            facts=[f.statement for f in facts] if eligible else [],
            source_item_ids=ids,
            content_format="post",
            media_intent=media, audience=view["audience"],
            language=view["language"],
            priority="high" if eligible and len(facts) >= 3
            else "normal",
            confidence=confidence if eligible else "low",
            constraints=constraints, model="mock",
            meta={"reason": reason, "pillar": pillar,
                  "fact_count": len(facts)})


class OpenAIAnalysisModel(AnalysisModel):
    """Analysis role over OpenAI chat (own prompt/contract; R3
    transport style only). Strict validation; failures raise
    AnalysisError and change nothing."""

    def __init__(self, api_key: str = "", model: str = "gpt-4.1"):
        if not api_key:
            raise AnalysisError("OPENAI_API_KEY is not set")
        self.api_key = api_key
        self.model = model

    def analyze(self, result, policy: dict,
                items: list | None = None) -> ContentOpportunity:
        if result is None or not isinstance(
                getattr(result, "findings", None), list):
            raise AnalysisError("malformed-research")
        view = _policy_view(policy)
        findings = _findings_of(result)[:MAX_FINDINGS]
        if not findings:
            raise AnalysisError("empty-research")
        known = set()
        parts = []
        for f in findings:
            for i in (f.item_ids or []):
                known.add(str(i))
            parts.append(
                f"- [{f.kind}] {f.statement[:300]} "
                f"(items: {', '.join(str(i) for i in (f.item_ids or []))})")
        media_hint = _media_intent(view, items)
        prompt = (
            "You decide content opportunity. Reply with JSON ONLY, "
            "exactly: {\"eligible\": bool, \"topic\": str, \"angle\": "
            "str, \"rationale\": str, \"facts\": [str, source-backed "
            "only], \"source_item_ids\": [ids from input], "
            "\"content_format\": \"post\", \"media_intent\": "
            "\"none\"|\"image\"|\"video\", \"audience\": str, "
            "\"language\": str, \"priority\": "
            "\"low\"|\"normal\"|\"high\", \"confidence\": "
            "\"high\"|\"medium\"|\"low\", \"constraints\": [str]}. "
            "Rules: eligible=true ONLY with >=1 input fact; facts "
            "must quote input findings (never invent); every "
            "source_item_id MUST come from the input; media_intent "
            "suggested baseline: " + media_hint + "; no captions, "
            "no hashtags, no schedule, no publishing.")
        context = (
            f"POLICY: audience={view['audience']} "
            f"language={view['language']} tone={view['tone']} "
            f"pillars={view['pillars']}\n"
            f"SUMMARY: {(getattr(result, 'summary', '') or '')[:500]}\n"
            f"FINDINGS:\n" + "\n".join(parts))
        if len(context) > MAX_PROMPT_CHARS:
            context = context[:MAX_PROMPT_CHARS]
            truncated = True
        else:
            truncated = False
        body = json.dumps({
            "model": self.model, "temperature": 0.2, "max_tokens": 800,
            "messages": [{"role": "system", "content": prompt},
                         {"role": "user", "content": context}],
        }).encode()
        try:
            from urllib.request import Request, urlopen
            req = Request(
                "https://api.openai.com/v1/chat/completions", data=body,
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"})
            with urlopen(req, timeout=90) as r:
                raw = r.read().decode()
        except (TimeoutError, socket.timeout):
            raise AnalysisError("model-timeout")
        except Exception as e:
            raise AnalysisError(f"model-error: {type(e).__name__}")
        return self._validate(raw, known, truncated)

    def _validate(self, raw: str, known: set,
                  truncated: bool) -> ContentOpportunity:
        try:
            data = json.loads(raw)
        except Exception:
            raise AnalysisError("invalid-output")
        if not isinstance(data, dict):
            raise AnalysisError("invalid-output")
        if "choices" in data:
            try:
                inner = data["choices"][0]["message"]["content"]
                data = json.loads(inner)
            except Exception:
                raise AnalysisError("invalid-output")
            if not isinstance(data, dict):
                raise AnalysisError("invalid-output")
        try:
            eligible = data["eligible"]
            if not isinstance(eligible, bool):
                raise AnalysisError("invalid-output")
            ids = data.get("source_item_ids", [])
            if not isinstance(ids, list) or \
                    not all(isinstance(i, str) and i in known
                            for i in ids):
                raise AnalysisError("invalid-output")
            facts = data.get("facts", [])
            if not isinstance(facts, list) or \
                    not all(isinstance(f, str) and f.strip()
                            for f in facts):
                raise AnalysisError("invalid-output")
            if eligible and not facts:
                raise AnalysisError("invalid-output")
            media = data.get("media_intent", "none")
            prio = data.get("priority", "normal")
            conf = data.get("confidence", "low")
            fmt = data.get("content_format", "post")
            if media not in MEDIA_INTENTS or prio not in PRIORITIES \
                    or conf not in CONFIDENCES or fmt not in \
                    ELIGIBLE_FORMATS:
                raise AnalysisError("invalid-output")
            for key in ("topic", "angle", "rationale", "audience",
                        "language"):
                if not isinstance(data.get(key), str):
                    raise AnalysisError("invalid-output")
            constraints = data.get("constraints", [])
            if not isinstance(constraints, list) or \
                    not all(isinstance(c, str) for c in constraints):
                raise AnalysisError("invalid-output")
        except AnalysisError:
            raise
        except Exception:
            raise AnalysisError("invalid-output")
        return ContentOpportunity(
            eligible=eligible, topic=data["topic"].strip()[:200],
            angle=data["angle"].strip()[:300],
            rationale=data["rationale"].strip()[:500],
            facts=[f.strip()[:500] for f in facts] if eligible else [],
            source_item_ids=list(ids),
            content_format=fmt, media_intent=media,
            audience=data["audience"].strip()[:120],
            language=data["language"].strip()[:16],
            priority=prio, confidence=conf if eligible else "low",
            constraints=[c[:120] for c in constraints],
            model=self.model,
            meta={"truncated": truncated})

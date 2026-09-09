"""Research Model (R3): SourceItems -> structured findings.

Role boundary: this layer answers "WHAT DID WE FIND?" It
summarizes, extracts facts, and records model inference WITHOUT
deciding anything downstream (no publish/format/platform/schedule
choices — those belong to the future Analysis phase and G2).

Rules enforced here and in tests:
- input is already-collected material only (never fetches URLs,
  never touches monitoring state, never calls Tuzzina);
- output is strictly validated structured data (no prose trust);
- every finding traces to input item ids (fact vs inference is
  explicit, so Analysis can never mistake a guess for a source);
- confidence is a controlled vocabulary (high|medium|low), never
  invented decimals;
- failures raise ResearchError and change nothing downstream.

Transport: OpenAIResearchModel speaks OpenAI chat directly in the
same stdlib style as the (temporary) text generator, but as a
SEPARATE role with its own prompt/contract — the two roles are
never merged. MockResearchModel is deterministic for tests.
"""
from __future__ import annotations
import json
import re
import socket
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

FACT = "fact"
INFERENCE = "inference"
HIGH = "high"
MEDIUM = "medium"
LOW = "low"

MAX_ITEMS = 20
MAX_TEXT_CHARS = 2000
MAX_FINDINGS = 50

_STOP = {"the", "and", "for", "with", "this", "that", "from", "into",
        "your", "you", "are", "was", "were", "has", "have", "will",
        "about", "there", "their", "what", "when", "which", "video",
        "على", "من", "في", "إلى", "أن", "ما", "هذا", "هذه", "التي",
        "الذي", "مع", "بين", "بعد", "قبل", "عند", "كل", "غير"}


class ResearchError(Exception):
    """Research failure: empty/malformed input, model timeout or
    failure, invalid structured output, oversized context. Raising
    never marks source items as processed (this layer owns no
    monitoring state at all)."""


@dataclass
class Finding:
    statement: str
    kind: str  # FACT | INFERENCE
    confidence: str  # HIGH | MEDIUM | LOW
    item_ids: list = field(default_factory=list)
    excerpt: str = ""


@dataclass
class ResearchResult:
    summary: str
    findings: list = field(default_factory=list)  # [Finding]
    topics: list = field(default_factory=list)  # [str]
    entities: list = field(default_factory=list)  # [str]
    item_ids: list = field(default_factory=list)  # inputs covered
    earliest_published: str = ""
    latest_published: str = ""
    model: str = ""
    meta: dict = field(default_factory=dict)


class ResearchModel(ABC):
    @abstractmethod
    def research(self, items: list) -> ResearchResult:
        """Interpret already-collected SourceItems. Pure decision
        over input data: no fetch, no state, no Tuzzina."""


def _trace_id(item) -> str:
    return (getattr(item, "item_id", "") or
            getattr(item, "source_id", "") or
            getattr(item, "source_url", ""))


def _check_items(items) -> list:
    if not isinstance(items, list) or not items:
        raise ResearchError("empty-input")
    if len(items) > MAX_ITEMS * 4:
        raise ResearchError("oversized-context")
    for i, it in enumerate(items):
        if not hasattr(it, "title") or not hasattr(it, "text"):
            raise ResearchError(f"malformed-item-{i}")
    return items


def _bound_text(s: str) -> tuple[str, bool]:
    s = s or ""
    if len(s) > MAX_TEXT_CHARS:
        return s[:MAX_TEXT_CHARS], True
    return s, False


def _tokens(s: str) -> list[str]:
    return [t.strip(".,!?:;()\"'").lower() for t in (s or "").split()]


def _keywords(items, limit: int = 5) -> list[str]:
    counts: dict[str, int] = {}
    for it in items:
        for t in _tokens(f"{it.title} {it.text}"):
            if len(t) > 4 and t not in _STOP:
                counts[t] = counts.get(t, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [w for w, _ in ranked[:limit]]


def _entities(items) -> list[str]:
    found: list[str] = []
    for it in items:
        for m in re.finditer(r'"([^"]{3,60})"', it.text or ""):
            if m.group(1) not in found:
                found.append(m.group(1))
        for m in re.finditer(r"(?<!\w)@([\w]{2,30})", it.text or ""):
            h = "@" + m.group(1)
            if h not in found:
                found.append(h)
    return found[:10]


def _date_range(items) -> tuple[str, str]:
    dates = sorted({it.published_at for it in items
                    if getattr(it, "published_at", "")})
    if not dates:
        return "", ""
    return dates[0], dates[-1]


class MockResearchModel(ResearchModel):
    """Deterministic findings for tests: one FACT per item (title
    as claim, lead excerpt, trace id) plus one INFERENCE naming the
    shared top topic across all inputs. No network, no randomness."""

    def research(self, items: list) -> ResearchResult:
        items = _check_items(list(items))
        findings: list[Finding] = []
        for it in items:
            tid = _trace_id(it)
            if not tid:
                raise ResearchError("missing-trace-id")
            text, _ = _bound_text(it.text)
            findings.append(Finding(
                statement=(it.title or "").strip()[:300],
                kind=FACT,
                confidence=HIGH if it.published_at else MEDIUM,
                item_ids=[tid],
                excerpt=text[:200]))
        topics = _keywords(items)
        if topics:
            findings.append(Finding(
                statement=f"Shared topic across inputs: {topics[0]}",
                kind=INFERENCE, confidence=LOW,
                item_ids=[_trace_id(it) for it in items], excerpt=""))
        earliest, latest = _date_range(items)
        return ResearchResult(
            summary=(f"{len(items)} item(s): " +
                     "; ".join((it.title or "").strip()[:80]
                               for it in items))[:500],
            findings=findings, topics=topics,
            entities=_entities(items),
            item_ids=[_trace_id(it) for it in items],
            earliest_published=earliest, latest_published=latest,
            model="mock",
            meta={"inputs": len(items), "truncated": False})


class OpenAIResearchModel(ResearchModel):
    """Research role (separate prompt/contract from text generation).
    Transport runs through an injected llm adapter (default:
    OpenAI). The `adapter` carries its own credential+model; an
    explicitly passed adapter wins entirely. Returns strictly
    validated ResearchResult or raises ResearchError. Never fetches,
    never stores, never publishes."""

    def __init__(self, api_key: str = "", model: str = "gpt-4.1",
                 adapter=None):
        if adapter is None:
            if not api_key:
                raise ResearchError("OPENAI_API_KEY is not set")
            from llm.adapters import OpenAIAdapter
            adapter = OpenAIAdapter(api_key, model)
        self._adapter = adapter
        self.model = getattr(adapter, "model", model)

    def research(self, items: list) -> ResearchResult:
        items = _check_items(list(items))[:MAX_ITEMS]
        blocks, truncated = [], False
        for it in items:
            text, cut = _bound_text(it.text)
            truncated = truncated or cut
            blocks.append(
                f"ID: {_trace_id(it)}\nTitle: {(it.title or '').strip()}\n"
                f"Published: {it.published_at or 'unknown'}\n"
                f"Text: {text}")
        prompt = (
            "You research collected source items. Reply with JSON ONLY, "
            "exactly: {\"summary\": str, \"findings\": [{\"statement\": "
            "str, \"kind\": \"fact\"|\"inference\", \"confidence\": "
            "\"high\"|\"medium\"|\"low\", \"item_ids\": [ids from the "
            "input], \"excerpt\": str}], \"topics\": [str], \"entities\": "
            "[str]}. Rules: kind=fact ONLY for claims stated verbatim in "
            "the item text (excerpt must quote it); kind=inference for "
            "anything you conclude, with confidence low unless two or "
            "more items support it. Every finding MUST list only input "
            "IDs. No other keys, no prose outside the JSON.")
        body_text = "\n\n---\n\n".join(blocks)
        try:
            from llm.adapters import LLMError
            raw = self._adapter.complete(
                prompt, body_text, temperature=0.2, max_tokens=1500,
                timeout=90)
        except (TimeoutError, socket.timeout):
            raise ResearchError("model-timeout")
        except LLMError as e:
            raise ResearchError(f"model-error: {e}")
        return self._validate(raw, items, truncated)

    def _validate(self, raw: str, items: list,
                  truncated: bool) -> ResearchResult:
        # The chat API wraps our JSON in an envelope; unwrap exactly
        # one level, then validate strictly. Anything else is
        # invalid output (never coerced, never trusted).
        try:
            data = json.loads(raw)
        except Exception:
            raise ResearchError("invalid-output")
        if not isinstance(data, dict):
            raise ResearchError("invalid-output")
        if "choices" in data:
            try:
                inner = data["choices"][0]["message"]["content"]
                data = json.loads(inner)
            except Exception:
                raise ResearchError("invalid-output")
            if not isinstance(data, dict):
                raise ResearchError("invalid-output")
        known = {_trace_id(it) for it in items}
        findings: list[Finding] = []
        raw_findings = data.get("findings")
        if not isinstance(raw_findings, list) or not raw_findings:
            raise ResearchError("invalid-output")
        for f in raw_findings[:MAX_FINDINGS]:
            if not isinstance(f, dict):
                raise ResearchError("invalid-output")
            kind = f.get("kind")
            conf = f.get("confidence")
            ids = f.get("item_ids")
            stmt = f.get("statement")
            if kind not in (FACT, INFERENCE) or \
                    conf not in (HIGH, MEDIUM, LOW) or \
                    not isinstance(ids, list) or not ids or \
                    not all(isinstance(i, str) and i in known
                            for i in ids) or \
                    not isinstance(stmt, str) or not stmt.strip():
                raise ResearchError("invalid-output")
            excerpt = f.get("excerpt", "")
            findings.append(Finding(
                statement=stmt.strip()[:500], kind=kind, confidence=conf,
                item_ids=list(ids),
                excerpt=excerpt[:300] if isinstance(excerpt, str)
                else ""))
        topics = data.get("topics", [])
        entities = data.get("entities", [])
        if not isinstance(topics, list) or not isinstance(entities, list) \
                or not all(isinstance(t, str) for t in topics) or \
                not all(isinstance(e, str) for e in entities):
            raise ResearchError("invalid-output")
        summary = data.get("summary", "")
        if not isinstance(summary, str) or not summary.strip():
            raise ResearchError("invalid-output")
        earliest, latest = _date_range(items)
        return ResearchResult(
            summary=summary.strip()[:1000], findings=findings,
            topics=[t[:60] for t in topics[:10]],
            entities=[e[:60] for e in entities[:10]],
            item_ids=sorted(known),
            earliest_published=earliest, latest_published=latest,
            model=self.model,
            meta={"inputs": len(items), "truncated": truncated})

"""LLM execution adapters (Phase 1): one thin protocol translator
per vendor behind a single transport contract.

  adapter.complete(system, user, *, temperature, max_tokens,
                   timeout) -> str

Each adapter owns its endpoint, auth, request body, text
extraction, stop/finish-reason gating, and error translation.
Role prompts, validation, and domain contracts live above this
layer and never see provider shapes.

Dispatch (ADAPTERS) maps closed adapter_key -> class. It holds
CODE POINTERS ONLY: no models, no capabilities, no limits, no
feature matrix. Unknown keys fail closed.

Shared helper _post_json owns HTTP POST + JSON + timeout + HTTP
status normalization ONLY. Auth, schemas, and extraction stay in
the adapters. No retry anywhere (fail-fast, like every Brain
network call): timeouts propagate raw so roles keep their
existing *-timeout categories.
"""
from __future__ import annotations
import json
import re
import socket
import urllib.error
import urllib.request


class LLMError(Exception):
    """Transport/provider failure with a short safe detail
    (e.g. "http-401", "http-400: <redacted provider excerpt>",
    "refusal:end_turn", "empty-content").
    Never carries keys, headers, or bodies."""


PRODUCT_UA = "Tuzzina/1.0 (+https://www.juzzir.com)"


# Error-body handling: non-2xx responses keep a short excerpt so
# a bare status is diagnosable. Only the body text is kept (never
# headers), secrets are redacted, and the excerpt is capped: the
# full provider body is never stored or logged.
_ERROR_BODY_BYTES = 4096
_ERROR_EXCERPT_CHARS = 500

_REDACTED = "[REDACTED]"

_SECRET_PATTERNS = (
    # Bearer tokens and sk-style keys.
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/=]+"),
    re.compile(r"\bsk-[A-Za-z0-9\-_]+"),
    # Key assignments: api-key / x-api-key / password / secret /
    # credential / access_token followed by a delimiter and a value.
    re.compile(r"(?i)\b(api[_-]?key|x-api-key|password|passwd|"
               r"secret|credential|access[_-]?token)"
               r"([\"'\s:=]+)([^\"'\s,}]+)"),
)


def _safe_error_excerpt(raw: bytes) -> str:
    """Single-line redacted excerpt of a provider error body.
    Never raises; empty input yields an empty string."""
    try:
        text = (raw or b"").decode("utf-8", errors="replace")
    except Exception:
        return ""
    text = " ".join(text.split())
    if not text:
        return ""
    return _redact_secrets(text)[:_ERROR_EXCERPT_CHARS].strip()


def _redact_secrets(text: str) -> str:
    """Redact credential-shaped values. Shared by error bodies
    and structural sketches: keys/labels pass through, values
    that look like tokens do not."""
    text = _SECRET_PATTERNS[0].sub("Bearer " + _REDACTED, text)
    text = _SECRET_PATTERNS[1].sub("sk-" + _REDACTED, text)
    return _SECRET_PATTERNS[2].sub(r"\1\2" + _REDACTED, text)


def _http_error_suffix(err: object) -> str:
    """`: <excerpt>` for an HTTPError with a readable body, else
    empty (preserves the bare `http-<status>` contract)."""
    try:
        raw = err.read(_ERROR_BODY_BYTES)  # type: ignore[union-attr]
    except Exception:
        return ""
    excerpt = _safe_error_excerpt(raw)
    return (": " + excerpt) if excerpt else ""


_SKETCH_KEYS = 20
_SKETCH_NAME_CHARS = 32
_SKETCH_TYPES = 10
_SKETCH_TOTAL_CHARS = 300


def _type_name(value: object, limit: int = 16) -> str:
    return type(value).__name__[:limit]


def _label(value: object, limit: int = _SKETCH_NAME_CHARS) -> str:
    text = value if isinstance(value, str) else _type_name(value, limit)
    return " ".join(str(text).split())[:limit] or "?"


def _structure_sketch(data: object) -> str:
    """Safe structural sketch of a textless Responses payload:
    key names, counts, and type labels only — never content,
    values, headers, or secrets. Bounded output. Never raises,
    so diagnostics can never mask the underlying failure."""
    try:
        sketch = _sketch_body(data)
    except Exception:
        return ""
    if not sketch:
        return ""
    return ": " + _redact_secrets(sketch)[:_SKETCH_TOTAL_CHARS].strip()


def unwrap_model_json(raw: str) -> str:
    """Strip markdown code fences around model JSON output.
    Some models wrap their JSON-only reply in ```json fences
    despite JSON-only instructions; the strict validators reject
    anything that is not parseable, so fences are removed here
    before parsing. Returns the stripped text unchanged when no
    fence is present. Never raises."""
    try:
        text = (raw or "").strip()
        if not text.startswith("```"):
            return text
        first_nl = text.find("\n")
        if first_nl == -1:
            return ""
        text = text[first_nl + 1:]
        last = text.rfind("```")
        if last != -1:
            text = text[:last]
        return text.strip()
    except Exception:
        return raw or ""


def _sketch_body(data: object) -> str:
    if not isinstance(data, dict):
        return "typeof=" + _type_name(data)
    parts = []
    keys = sorted(_label(k) for k in data.keys())[:_SKETCH_KEYS]
    parts.append("keys=[" + ",".join(keys) + "]")
    status = data.get("status")
    if isinstance(status, str) and status.strip():
        parts.append("status=" + _label(status))
    if "output" in data:
        parts.append("output=" + _sketch_output(data["output"]))
    if "output_text" in data:
        parts.append("output_text=typeof=" +
                     _type_name(data["output_text"]))
    if "error" in data:
        parts.append("error=" + _sketch_error(data["error"]))
    return " ".join(p for p in parts if p)


def _sketch_output(out: object) -> str:
    if not isinstance(out, list):
        return "typeof=" + _type_name(out)
    types: list = []
    blocks = 0
    block_types: list = []
    for item in out[:_SKETCH_KEYS]:
        if not isinstance(item, dict):
            types.append("typeof=" + _type_name(item))
            continue
        types.append(_label(item.get("type")))
        content = item.get("content")
        if isinstance(content, list):
            blocks += len(content)
            for block in content[:_SKETCH_KEYS]:
                bt = (block.get("type") if isinstance(block, dict)
                      else None)
                bt = _label(bt) if isinstance(bt, str) \
                    else "typeof=" + _type_name(block)
                if bt not in block_types and \
                        len(block_types) < _SKETCH_TYPES:
                    block_types.append(bt)
    return ("list[n=%d types=[%s] blocks=%d block_types=[%s]]"
            % (len(out), ",".join(types[:_SKETCH_TYPES]),
               blocks, ",".join(block_types)))


def _sketch_error(err: object) -> str:
    if not isinstance(err, dict):
        return "typeof=" + _type_name(err)
    keys = sorted(_label(k) for k in err.keys())[:10]
    return "dict[keys=[" + ",".join(keys) + "]]"


def _post_json(url: str, headers: dict, body: dict,
               timeout: int) -> object:
    """POST JSON, return parsed JSON. Raw TimeoutError/socket.timeout
    propagate (roles map them to *-timeout). HTTP errors become
    LLMError("http-<status>[: <redacted body excerpt>]"). Anything
    else becomes LLMError("transport-<Type>")."""
    # Zen edge requires a product UA (Cloudflare blocks stdlib UA).
    # This is a provider HTTP concern, not model-specific; openai.com
    # and anthropic.com are unaffected because the header is only
    # added for Zen hosts and only when absent.
    if "opencode.ai/zen" in url and not any(k.lower() == "user-agent" for k in headers):
        headers = {**headers, "User-Agent": PRODUCT_UA}
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
    except (TimeoutError, socket.timeout):
        raise
    except urllib.error.HTTPError as e:
        raise LLMError(f"http-{e.code}{_http_error_suffix(e)}")
    except Exception as e:  # DNS/refused/reset: fail fast, named
        raise LLMError(f"transport-{type(e).__name__}")
    try:
        return json.loads(raw)
    except Exception:
        raise LLMError("malformed-response")


def _join_versioned(base: str, suffix: str) -> str:
    """Join an OpenAI-versioned path idempotently. Account base
    URLs may or may not include the trailing /v1 mount point
    (`https://host` and `https://host/v1` reach the same versioned
    path); class defaults carry no /v1, so built-in behavior is
    byte-identical. Provider-blind: pure path math."""
    base = (base or "").rstrip("/")
    if base.endswith("/v1") and suffix.startswith("/v1/"):
        base = base[: -len("/v1")]
    return base + suffix


class OpenAIAdapter:
    """OpenAI chat completions. Endpoint + Bearer auth + messages[]
    body + choices[0].message.content extraction."""

    adapter_key = "openai"
    base_url = "https://api.openai.com"

    def __init__(self, api_key: str = "", model: str = "gpt-4.1",
                  base_url: str = "", session_id: str = ""):
        if not api_key:
            raise ValueError("api-key-required")
        self.api_key = api_key
        self.model = model
        self.base = (base_url or self.base_url).rstrip("/")
        # Automation run identity for providers that require a
        # session header. Plain OpenAI ignores it (see
        # _extra_headers): only provider subclasses opt in.
        self.session_id = session_id or ""

    def _extra_headers(self) -> dict:
        return {}

    def complete(self, system: str, user: str, *,
                  temperature: float = 0.7, max_tokens: int = 400,
                  timeout: int = 60) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        headers.update(self._extra_headers())
        data = _post_json(
            _join_versioned(self.base, "/v1/chat/completions"),
            headers,
            {"model": self.model, "temperature": temperature,
              "max_tokens": max_tokens,
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": user}]},
            timeout)
        try:
            text = data["choices"][0]["message"]["content"]
        except Exception:
            raise LLMError("malformed-response")
        if not isinstance(text, str) or not text.strip():
            raise LLMError("empty-content")
        return text


class AnthropicAdapter:
    """Anthropic Messages API. Structurally different protocol:
    x-api-key + anthropic-version headers, top-level system field,
    required max_tokens, content[] blocks, stop_reason gating."""

    adapter_key = "anthropic"
    base_url = "https://api.anthropic.com"
    api_version = "2023-06-01"

    def __init__(self, api_key: str = "", model: str = "",
                  base_url: str = "", session_id: str = ""):
        if not api_key:
            raise ValueError("api-key-required")
        self.api_key = api_key
        self.model = model
        self.base = (base_url or self.base_url).rstrip("/")
        # Accepted for uniform construction only. Anthropic defines
        # no session header, so complete() never reads this.
        self.session_id = session_id or ""

    def complete(self, system: str, user: str, *,
                 temperature: float = 0.7, max_tokens: int = 400,
                 timeout: int = 60) -> str:
        data = _post_json(
            self.base + "/v1/messages",
            {"x-api-key": self.api_key,
             "anthropic-version": self.api_version,
             "Content-Type": "application/json"},
            {"model": self.model, "max_tokens": max_tokens,
             "system": system, "temperature": temperature,
             "messages": [{"role": "user", "content": user}]},
            timeout)
        if not isinstance(data, dict):
            raise LLMError("malformed-response")
        stop = data.get("stop_reason")
        if stop != "end_turn":
            # refusal / max_tokens / tool_use / anything else is not
            # a trustworthy completion for our roles: loud, no text.
            raise LLMError(f"refusal:{stop}")
        parts = []
        content = data.get("content")
        if not isinstance(content, list):
            raise LLMError("malformed-response")
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" \
                    and isinstance(block.get("text"), str):
                parts.append(block["text"])
        text = "".join(parts)
        if not text.strip():
            raise LLMError("empty-content")
        return text


class OpenAIResponsesAdapter:
    """OpenAI Responses API. Same auth as Chat Completions, different
    path and body: POST /v1/responses {model,input} → output[].content[].text.
    Shares no code with chat completions except auth/helper."""

    adapter_key = "openai"
    protocol = "responses"
    base_url = "https://api.openai.com"

    def __init__(self, api_key: str = "", model: str = "gpt-4.1",
                  base_url: str = "", session_id: str = ""):
        if not api_key:
            raise ValueError("api-key-required")
        self.api_key = api_key
        self.model = model
        self.base = (base_url or self.base_url).rstrip("/")
        # Same run-identity contract as OpenAIAdapter above.
        self.session_id = session_id or ""

    def _extra_headers(self) -> dict:
        return {}

    def complete(self, system: str, user: str, *,
                 temperature: float = 0.7, max_tokens: int = 400,
                 timeout: int = 60) -> str:
        # Responses separates system as instructions; keep role
        # semantics instead of concatenating into one string.
        body: dict = {"model": self.model, "input": user}
        if system and system.strip():
            body["instructions"] = system
        # Preserve temperature where supported; max_tokens maps to
        # max_output_tokens on Responses (ignored if unsupported).
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens is not None:
            body["max_output_tokens"] = max_tokens
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        headers.update(self._extra_headers())
        data = _post_json(
            _join_versioned(self.base, "/v1/responses"),
            headers,
            body,
            timeout)
        try:
            # {output:[{content:[{type:text,text:str}]}]} or similar
            out = data.get("output") if isinstance(data, dict) else None
            text = ""
            if isinstance(out, list):
                for item in out:
                    if not isinstance(item, dict):
                        continue
                    for block in item.get("content", []) or []:
                        if isinstance(block, dict) and block.get("type") in ("text", "output_text") \
                                and isinstance(block.get("text"), str):
                            text += block["text"]
            # Fallback: some implementations return {output_text:str}
            if not text and isinstance(data, dict) and isinstance(data.get("output_text"), str):
                text = data["output_text"]
        except Exception:
            raise LLMError("malformed-response")
        if not isinstance(text, str) or not text.strip():
            raise LLMError("empty-content" + _structure_sketch(data))
        return text


class OpenCodeZenAdapter(OpenAIAdapter):
    """OpenCode Zen via its OpenAI-compatible chat completions
    surface. Same protocol as OpenAIAdapter (Bearer auth,
    messages[] body, choices[] extraction) against Zen's base
    URL; the model id passes through unchanged from the role
    assignment. No default model: Zen serves many model ids and
    inventing one would silently pick a vendor. An empty or
    unsupported model fails closed as a provider error."""

    adapter_key = "opencode_zen"
    protocol = "chat_completions"
    base_url = "https://opencode.ai/zen"

    def __init__(self, api_key: str = "", model: str = "",
                 base_url: str = "", session_id: str = ""):
        super().__init__(api_key, model, base_url or self.base_url,
                         session_id)

    def _extra_headers(self) -> dict:
        # Zen gateway requirement (free-tier session routing).
        # Provider-scoped: no other adapter emits this header.
        if self.session_id:
            return {"x-opencode-session": self.session_id}
        return {}


class OpenCodeZenResponsesAdapter(OpenAIResponsesAdapter):
    """OpenCode Zen via Responses surface. Same as OpenAIResponses
    but against Zen's base URL."""

    adapter_key = "opencode_zen"
    protocol = "responses"
    base_url = "https://opencode.ai/zen"

    def _extra_headers(self) -> dict:
        if self.session_id:
            return {"x-opencode-session": self.session_id}
        return {}


# Generic protocol dispatch (provider,protocol) -> class.
# Model id stays opaque; protocol is an explicit assignment dimension.
OpenAIAdapter.protocol = "chat_completions"
AnthropicAdapter.protocol = "messages"

PROTOCOL_ADAPTERS = {
    (OpenAIAdapter.adapter_key, OpenAIAdapter.protocol): OpenAIAdapter,
    (OpenAIResponsesAdapter.adapter_key, OpenAIResponsesAdapter.protocol): OpenAIResponsesAdapter,
    (AnthropicAdapter.adapter_key, AnthropicAdapter.protocol): AnthropicAdapter,
    (OpenCodeZenAdapter.adapter_key, OpenCodeZenAdapter.protocol): OpenCodeZenAdapter,
    (OpenCodeZenResponsesAdapter.adapter_key, OpenCodeZenResponsesAdapter.protocol): OpenCodeZenResponsesAdapter,
    # Generic OpenAI-compatible connections reuse the generic
    # OpenAI-compatible transports against an account-level base
    # URL. No vendor adapter exists or is needed.
    ("openai_compatible", "chat_completions"): OpenAIAdapter,
    ("openai_compatible", "responses"): OpenAIResponsesAdapter,
}

LEGACY_PROTOCOL = {
    "openai": "chat_completions",
    "opencode_zen": "chat_completions",
    "openai_compatible": "chat_completions",
    "anthropic": "messages",
}

ADAPTERS = {
    OpenAIAdapter.adapter_key: OpenAIAdapter,
    AnthropicAdapter.adapter_key: AnthropicAdapter,
    OpenCodeZenAdapter.adapter_key: OpenCodeZenAdapter,
}


def resolve_adapter(adapter_key: str, protocol: str | None = None):
    """Closed dispatch: (key,protocol) -> class. Unknown keys or
    unsupported protocol combinations fail closed. When protocol is
    None, legacy mapping preserves behavior for old assignments."""
    if protocol is not None:
        if not isinstance(protocol, str) or not protocol:
            raise ValueError(f"unknown-llm-adapter:{adapter_key}:{protocol}")
        try:
            return PROTOCOL_ADAPTERS[(adapter_key, protocol)]
        except (KeyError, TypeError):
            raise ValueError(f"unknown-llm-adapter:{adapter_key}:{protocol}")
    # Legacy single-arg path: explicit mapping, not array order
    legacy = LEGACY_PROTOCOL.get(adapter_key)
    if legacy is not None:
        try:
            return PROTOCOL_ADAPTERS[(adapter_key, legacy)]
        except Exception:
            pass
        # Fallback for tuzzina-absent case handled above; keep ADAPTERS
        pass
    # Backward compat: direct key lookup
    try:
        return ADAPTERS[adapter_key]
    except (KeyError, TypeError):
        raise ValueError(f"unknown-llm-adapter:{adapter_key}")


def resolve_protocol_adapter(adapter_key: str, protocol: str):
    """Explicit (provider,protocol) dispatch."""
    return resolve_adapter(adapter_key, protocol)

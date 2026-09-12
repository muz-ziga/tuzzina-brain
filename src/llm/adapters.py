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
import socket
import urllib.error
import urllib.request


class LLMError(Exception):
    """Transport/provider failure with a short safe detail
    (e.g. "http-401", "refusal:end_turn", "empty-content").
    Never carries keys, headers, or bodies."""


PRODUCT_UA = "Tuzzina/1.0 (+https://www.juzzir.com)"


def _post_json(url: str, headers: dict, body: dict,
               timeout: int) -> object:
    """POST JSON, return parsed JSON. Raw TimeoutError/socket.timeout
    propagate (roles map them to *-timeout). HTTP errors become
    LLMError("http-<status>"). Anything else becomes
    LLMError("transport-<Type>")."""
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
        raise LLMError(f"http-{e.code}")
    except Exception as e:  # DNS/refused/reset: fail fast, named
        raise LLMError(f"transport-{type(e).__name__}")
    try:
        return json.loads(raw)
    except Exception:
        raise LLMError("malformed-response")


class OpenAIAdapter:
    """OpenAI chat completions. Endpoint + Bearer auth + messages[]
    body + choices[0].message.content extraction."""

    adapter_key = "openai"
    base_url = "https://api.openai.com"

    def __init__(self, api_key: str = "", model: str = "gpt-4.1",
                 base_url: str = ""):
        if not api_key:
            raise ValueError("api-key-required")
        self.api_key = api_key
        self.model = model
        self.base = (base_url or self.base_url).rstrip("/")

    def complete(self, system: str, user: str, *,
                 temperature: float = 0.7, max_tokens: int = 400,
                 timeout: int = 60) -> str:
        data = _post_json(
            self.base + "/v1/chat/completions",
            {"Authorization": f"Bearer {self.api_key}",
             "Content-Type": "application/json"},
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
                 base_url: str = ""):
        if not api_key:
            raise ValueError("api-key-required")
        self.api_key = api_key
        self.model = model
        self.base = (base_url or self.base_url).rstrip("/")

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
                 base_url: str = ""):
        if not api_key:
            raise ValueError("api-key-required")
        self.api_key = api_key
        self.model = model
        self.base = (base_url or self.base_url).rstrip("/")

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
        data = _post_json(
            self.base + "/v1/responses",
            {"Authorization": f"Bearer {self.api_key}",
             "Content-Type": "application/json"},
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
            raise LLMError("empty-content")
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
                 base_url: str = ""):
        super().__init__(api_key, model, base_url or self.base_url)


class OpenCodeZenResponsesAdapter(OpenAIResponsesAdapter):
    """OpenCode Zen via Responses surface. Same as OpenAIResponses
    but against Zen's base URL."""

    adapter_key = "opencode_zen"
    protocol = "responses"
    base_url = "https://opencode.ai/zen"


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
}

LEGACY_PROTOCOL = {
    "openai": "chat_completions",
    "opencode_zen": "chat_completions",
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

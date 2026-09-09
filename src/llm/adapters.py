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


def _post_json(url: str, headers: dict, body: dict,
               timeout: int) -> object:
    """POST JSON, return parsed JSON. Raw TimeoutError/socket.timeout
    propagate (roles map them to *-timeout). HTTP errors become
    LLMError("http-<status>"). Anything else becomes
    LLMError("transport-<Type>")."""
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


ADAPTERS = {
    OpenAIAdapter.adapter_key: OpenAIAdapter,
    AnthropicAdapter.adapter_key: AnthropicAdapter,
}


def resolve_adapter(adapter_key: str):
    """Closed dispatch: key -> adapter class. Unknown keys fail
    closed (no default, no guessing)."""
    try:
        return ADAPTERS[adapter_key]
    except (KeyError, TypeError):
        raise ValueError(f"unknown-llm-adapter:{adapter_key}")

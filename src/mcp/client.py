"""Generic MCP client (Stage B). One reusable JSON-RPC 2.0
client for ANY MCP server: initialize -> tools/list ->
tools/call, with session handling, timeouts, size caps, and
fail-closed machine errors.

Extracted from the proven Tuzzina image-delegation handshake
(same URLs, headers, payloads, session semantics); behavior
for that caller is byte-identical.

Deliberate non-goals: no SSE/streaming transports (refused
loudly), no automatic retries (generation is not
idempotent), no credential storage (the caller supplies one
auth header value; it never appears in errors, traces, or
state). Configured endpoints are operator-trusted
destinations (scheme + no-userinfo validated); untrusted
*content* fetched from elsewhere still goes through
g1.fetch, never through this client.
"""
from __future__ import annotations
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

PROTOCOL_VERSION = "2024-11-05"
CLIENT_NAME = "tuzzina-brain"
CLIENT_VERSION = "0.11.0"
DEFAULT_TIMEOUT = 60
DEFAULT_MAX_BYTES = 1_000_000


class McpError(Exception):
    """Machine reason only. Never carries bodies, headers,
    credentials, or response content beyond a 300-char
    diagnostic excerpt (see _excerpt)."""


def _excerpt(value) -> str:
    try:
        text = value if isinstance(value, str) else json.dumps(value)
    except Exception:
        return ""
    return (text or "")[:300]


class McpClient:
    """One MCP server connection. Stateless across calls except
    the server-issued session id, which reset() drops (next
    call re-initializes). Not thread-safe by design: one
    instance per sequential flow (matches the single-threaded
    slot executor)."""

    def __init__(self, endpoint: str, *, auth_header: str = "",
                 timeout: int = DEFAULT_TIMEOUT,
                 max_bytes: int = DEFAULT_MAX_BYTES,
                 user_agent: str = "tuzzina-brain/0.1 (+mcp)"):
        p = urllib.parse.urlparse((endpoint or "").strip())
        if p.scheme not in ("http", "https") or not p.hostname:
            raise McpError("unsafe-endpoint")
        if "@" in (p.netloc or ""):
            raise McpError("unsafe-endpoint")
        if timeout is None or timeout <= 0:
            timeout = DEFAULT_TIMEOUT
        if max_bytes is None or max_bytes <= 0:
            max_bytes = DEFAULT_MAX_BYTES
        self.endpoint = endpoint.strip()
        self.auth_header = auth_header or ""
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.user_agent = user_agent or "tuzzina-brain/0.1 (+mcp)"
        self._session_id = ""
        self._next_id = 1

    def reset(self) -> None:
        """Drop server session state. Idempotent, never raises."""
        try:
            self._session_id = ""
        except Exception:
            pass

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream",
                   "User-Agent": self.user_agent}
        if self.auth_header:
            headers["Authorization"] = self.auth_header
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _rpc(self, method: str, params=None,
             notify: bool = False) -> dict:
        payload: dict = {"jsonrpc": "2.0", "method": method}
        if not notify:
            payload["id"] = self._next_id
            self._next_id += 1
        if params is not None:
            payload["params"] = params
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.endpoint, data=body, headers=self._headers(),
            method="POST")
        try:
            with urllib.request.urlopen(req,
                                        timeout=self.timeout) as r:
                ctype = ""
                try:
                    ctype = r.headers.get("Content-Type", "") or ""
                except Exception:
                    ctype = ""
                if "text/event-stream" in ctype and "json" not in ctype:
                    raise McpError("streaming-unsupported")
                try:
                    raw = r.read(self.max_bytes + 1)
                except (TimeoutError, socket.timeout):
                    raise McpError("timeout")
                try:
                    hdrs = getattr(r, "headers", {}) or {}
                    sid = hdrs.get("mcp-session-id") or \
                        hdrs.get("Mcp-Session-Id")
                    if sid:
                        self._session_id = str(sid)
                except Exception:
                    pass
                try:
                    text = raw.decode("utf-8", errors="replace")
                except Exception:
                    raise McpError("malformed-response")
                if len(raw) > self.max_bytes:
                    raise McpError("oversized-response")
                try:
                    data = json.loads(text) if text.strip() else {}
                except Exception:
                    raise McpError("malformed-response")
                if not isinstance(data, dict):
                    raise McpError("malformed-response")
                return data
        except McpError:
            raise
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode(
                    "utf-8", errors="replace")[:300]
            except Exception:
                detail = ""
            raise McpError(f"http-{e.code}:{detail}" if detail
                           else f"http-{e.code}")
        except (urllib.error.URLError, TimeoutError, ConnectionError,
                OSError) as e:
            name = type(e).__name__
            raise McpError(
                "timeout" if "imeout" in name else "unavailable")
        except Exception:
            raise McpError("unavailable")

    def initialize(self, client_info: dict | None = None) -> dict:
        """Handshake. Returns the server result dict. Raises
        McpError on any deviation (missing result envelope)."""
        info = dict(client_info or {})
        info.setdefault("name", CLIENT_NAME)
        info.setdefault("version", CLIENT_VERSION)
        data = self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": info})
        result = data.get("result")
        if not isinstance(result, dict):
            raise McpError("handshake-failed")
        try:
            self._rpc("notifications/initialized", notify=True)
        except McpError:
            raise
        except Exception:
            raise McpError("handshake-failed")
        return result

    def list_tools(self) -> list:
        """tools/list. Returns the raw tool descriptor list.
        Shape validation happens in the registry, not here."""
        self.initialize()
        data = self._rpc("tools/list")
        result = data.get("result") if isinstance(data, dict) else None
        tools = result.get("tools") if isinstance(result, dict) \
            else None
        if not isinstance(tools, list):
            raise McpError("malformed-tools")
        return tools

    def call_tool(self, name: str, arguments=None,
                  timeout: int | None = None) -> dict:
        """tools/call for one authorized tool. Returns the result
        envelope. Server-reported errors (error member or
        isError content) raise McpError tool-error:<excerpt>.
        No retries: callers own idempotency."""
        if not name or not isinstance(name, str):
            raise McpError("tool-name-required")
        if timeout is not None:
            saved, self.timeout = self.timeout, timeout
        else:
            saved = None
        try:
            self.initialize()
            data = self._rpc("tools/call", {
                "name": name,
                "arguments": arguments if isinstance(arguments, dict)
                else {}})
        finally:
            if saved is not None:
                self.timeout = saved
        if isinstance(data, dict) and data.get("error"):
            raise McpError(
                "tool-error:" + _excerpt(data.get("error")))
        result = (data.get("result") or {}) \
            if isinstance(data, dict) else {}
        if isinstance(result, dict) and result.get("isError"):
            detail = ""
            for item in result.get("content") or []:
                if isinstance(item, dict) and \
                        isinstance(item.get("text"), str):
                    detail = item["text"][:300]
                    break
            raise McpError(
                "tool-error:" + (detail or "tool error"))
        if not isinstance(result, dict):
            raise McpError("malformed-result")
        return result

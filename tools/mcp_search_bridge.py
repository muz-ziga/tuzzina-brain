"""Self-hosted MCP search bridge (Stage E deployment infra).

Exposes a SearXNG-compatible JSON search API as two generic MCP
tools over stdlib HTTP so ANY MCP client (Brain included) can
use semantic capabilities without vendor-specific code:

    search      -> capability web.search
    news_search -> capability news.search

This file is INFRASTRUCTURE, not Brain architecture: Brain
never imports it, never names it, and works with any MCP
server behind the same capability names (swap this file for
another provider by editing the registry only).

Backend: SEARXNG_BASE_URL (default http://127.0.0.1:8888),
queried via GET /search?q=..&format=json&categories=... .
Self-hosted SearXNG needs no API key and has no purchase
path; this bridge adds no key, no account, and no paid
fallback.

Fail-closed: SearXNG down/timeout/invalid -> JSON-RPC error
member (never fabricated results, never empty-success).
Untrusted SearXNG content passes through byte-identical;
quoting/filtering stays Brain-side (prompt-injection
boundary).

Usage:
    SEARXNG_BASE_URL=http://127.0.0.1:8888 python3
        tools/mcp_search_bridge.py [--port 8899]

Registry example (MCP_SERVERS_FILE), anonymous loopback:
    servers:
      - id: search-local
        transport: http
        endpoint: http://127.0.0.1:8899/mcp
        auth_env: ""
        enabled: true
        rate_limit: {per_minute: 30}
        tools:
          - {name: search, capability: web.search}
          - {name: news_search, capability: news.search}
"""
from __future__ import annotations
import json
import os
import sys
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "searxng-bridge"
SERVER_VERSION = "0.1.0"
TIMEOUT = 25
MAX_RESULTS = 20

TOOLS = [
    {"name": "search",
     "description": "Web search via self-hosted SearXNG. "
                    "Args: {query: str, max_results?: int}.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "query": {"type": "string"},
                         "max_results": {"type": "integer"}},
                     "required": ["query"]}},
    {"name": "news_search",
     "description": "News search via self-hosted SearXNG. "
                    "Args: {query: str, max_results?: int}.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "query": {"type": "string"},
                         "max_results": {"type": "integer"}},
                     "required": ["query"]}},
]


def _searxng_base() -> str:
    return (os.environ.get("SEARXNG_BASE_URL",
                           "http://127.0.0.1:8888").strip()
            .rstrip("/"))


def _search_backend(query: str, categories: str,
                    max_results: int) -> list:
    """Query SearXNG JSON API. Returns the raw result list.
    Raises RuntimeError(reason) on any failure: connection,
    timeout, HTTP error, malformed JSON, unexpected shape."""
    base = _searxng_base()
    if not base.startswith(("http://", "https://")):
        raise RuntimeError("bad-searxng-base-url")
    params = urllib.parse.urlencode(
        {"q": query, "format": "json", "categories": categories})
    url = base + "/search?" + params
    req = urllib.request.Request(
        url, headers={"User-Agent": "tuzzina-brain/0.1 (+mcp)",
                      "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req,
                                    timeout=TIMEOUT) as r:
            raw = r.read(1_000_000 + 1)
    except Exception as e:
        name = type(e).__name__
        raise RuntimeError(
            "timeout" if "imeout" in name else "searxng-unavailable")
    if len(raw) > 1_000_000:
        raise RuntimeError("oversized-response")
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        raise RuntimeError("malformed-response")
    if not isinstance(data, dict) or not isinstance(
            data.get("results"), list):
        raise RuntimeError("unexpected-shape")
    out = []
    for item in data["results"][:max(1, min(
            max_results, MAX_RESULTS))]:
        if not isinstance(item, dict):
            continue
        out.append({
            "title": item.get("title") if isinstance(
                item.get("title"), str) else "",
            "url": item.get("url") if isinstance(
                item.get("url"), str) else "",
            "content": item.get("content") if isinstance(
                item.get("content"), str) else "",
            "publishedDate": item.get("publishedDate") if
            isinstance(item.get("publishedDate"), str) else "",
            "engine": item.get("engine") if isinstance(
                item.get("engine"), str) else "",
        })
    return out


def _handle(payload: dict) -> dict:
    """One JSON-RPC request -> response dict (no envelope
    guessing: id echoed verbatim, errors as error member)."""
    pid = payload.get("id")
    method = payload.get("method")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": pid,
                "result": {"protocolVersion": PROTOCOL_VERSION,
                           "capabilities": {},
                           "serverInfo": {"name": SERVER_NAME,
                                          "version": SERVER_VERSION}}}
    if method == "notifications/initialized":
        return {}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": pid,
                "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = payload.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in ("search", "news_search"):
            return {"jsonrpc": "2.0", "id": pid,
                    "error": {"code": -32602,
                              "message": "unknown-tool"}}
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return {"jsonrpc": "2.0", "id": pid,
                    "error": {"code": -32602,
                              "message": "query-required"}}
        try:
            count = int(args.get("max_results", 10))
        except (TypeError, ValueError):
            count = 10
        categories = "news" if name == "news_search" else "general"
        try:
            results = _search_backend(query.strip(), categories,
                                      count)
        except RuntimeError as e:
            return {"jsonrpc": "2.0", "id": pid,
                    "error": {"code": -32000, "message": str(e)}}
        return {"jsonrpc": "2.0", "id": pid,
                "result": {"content": [
                    {"type": "text",
                     "text": json.dumps(results,
                                        ensure_ascii=False)}]}}
    return {"jsonrpc": "2.0", "id": pid,
            "error": {"code": -32601, "message": "unknown-method"}}


class _Handler(BaseHTTPRequestHandler):
    server_version = "McpSearchBridge/0.1"

    def log_message(self, *a):
        pass

    def _send(self, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/mcp":
            self.send_response(404)
            self.end_headers()
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            length = 0
        if length <= 0 or length > 1_000_000:
            self.send_response(400)
            self.end_headers()
            return
        try:
            payload = json.loads(
                self.rfile.read(length).decode("utf-8"))
        except Exception:
            self.send_response(400)
            self.end_headers()
            return
        if not isinstance(payload, dict):
            self.send_response(400)
            self.end_headers()
            return
        try:
            self._send(_handle(payload))
        except BrokenPipeError:
            pass


def main(argv=None) -> int:
    port = 8899
    args = list(argv or [])[1:]
    if args and args[0].isdigit():
        port = max(1, min(65535, int(args[0])))
    server = HTTPServer(("127.0.0.1", port), _Handler)
    print(f"mcp-search-bridge on 127.0.0.1:{port} "
          f"(searxng={_searxng_base()})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

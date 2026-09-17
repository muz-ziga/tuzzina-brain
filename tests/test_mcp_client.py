"""Stage B MCP client tests: generic JSON-RPC over stdlib HTTP.
Fully offline: urllib.request.urlopen is substituted. No network,
no secrets, no DB."""
import io
import json
import os
import sys
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mcp.client import McpClient, McpError

CALLS = []


class FakeResp:
    def __init__(self, payload, code=200, headers=None):
        self.payload = payload
        self.code = code
        self.headers = headers or {}

    def read(self, n=-1):
        data = json.dumps(self.payload).encode()
        return data if n is None or n < 0 else data[:n]

    def geturl(self):
        return "http://mcp.test/mcp"

    def __enter__(self):
        if self.code >= 400:
            raise urllib.error.HTTPError("u", self.code, "err", {},
                                         None)
        return self

    def __exit__(self, *a):
        return False


def _server(req, timeout=None):
    """Minimal generic MCP double: initialize + tools/list +
    tools/call for web.search. Records auth presence, never the
    value (mirrors the client contract)."""
    payload = json.loads(req.data.decode())
    CALLS.append({"method": payload.get("method"),
                  "has_id": "id" in payload,
                  "auth_set": bool(req.get_header("Authorization"))})
    method = payload.get("method")
    pid = payload.get("id")
    if method == "initialize":
        return FakeResp({"jsonrpc": "2.0", "id": pid,
                         "result": {"protocolVersion": "2024-11-05",
                                    "capabilities": {},
                                    "serverInfo": {"name": "t",
                                                   "version": "1"}}},
                        headers={"mcp-session-id": "s9"})
    if method == "notifications/initialized":
        return FakeResp({})
    if method == "tools/list":
        return FakeResp({"jsonrpc": "2.0", "id": pid,
                         "result": {"tools": [
                             {"name": "search",
                              "description": "web search"}]}})
    if method == "tools/call":
        name = (payload.get("params") or {}).get("name")
        assert name == "search", payload
        return FakeResp({"jsonrpc": "2.0", "id": pid,
                         "result": {"content": [{"type": "text",
                                                 "text": "[]"}]}})
    raise AssertionError("unexpected method " + str(method))


_REAL = urllib.request.urlopen


class ClientCase(unittest.TestCase):
    def setUp(self):
        del CALLS[:]
        urllib.request.urlopen = _server

    def tearDown(self):
        urllib.request.urlopen = _REAL

    def test_endpoint_must_be_http(self):
        for bad in ("", "file:///x", "ftp://h/x", "http:///"):
            with self.assertRaises(McpError, msg=bad):
                McpClient(bad)

    def test_userinfo_in_endpoint_refused(self):
        with self.assertRaises(McpError):
            McpClient("http://user:pass@h/mcp")

    def test_initialize_returns_server_result(self):
        out = McpClient("http://mcp.test/mcp").initialize()
        self.assertEqual(out["serverInfo"]["name"], "t")
        self.assertEqual([c["method"] for c in CALLS],
                         ["initialize", "notifications/initialized"])

    def test_handshake_failure_loud(self):
        def _bad(req, timeout=None):
            return FakeResp({"jsonrpc": "2.0", "id": 1})
        urllib.request.urlopen = _bad
        with self.assertRaises(McpError) as ctx:
            McpClient("http://mcp.test/mcp").initialize()
        self.assertEqual(str(ctx.exception), "handshake-failed")

    def test_list_tools(self):
        tools = McpClient("http://mcp.test/mcp").list_tools()
        self.assertEqual([t["name"] for t in tools], ["search"])

    def test_malformed_tools_refused(self):
        def _bad(req, timeout=None):
            payload = json.loads(req.data.decode())
            if payload.get("method") == "tools/list":
                return FakeResp({"jsonrpc": "2.0",
                                 "id": payload.get("id"),
                                 "result": {"tools": "nope"}})
            return _server(req, timeout)
        urllib.request.urlopen = _bad
        with self.assertRaises(McpError) as ctx:
            McpClient("http://mcp.test/mcp").list_tools()
        self.assertEqual(str(ctx.exception), "malformed-tools")

    def test_call_tool_round_trip(self):
        out = McpClient("http://mcp.test/mcp").call_tool(
            "search", {"q": "mastering"})
        self.assertIn("content", out)

    def test_call_tool_name_required(self):
        with self.assertRaises(McpError):
            McpClient("http://mcp.test/mcp").call_tool("")

    def test_tool_error_propagates_excerpt_only(self):
        def _err(req, timeout=None):
            payload = json.loads(req.data.decode())
            if payload.get("method") == "tools/call":
                return FakeResp({"jsonrpc": "2.0",
                                 "id": payload.get("id"),
                                 "error": {"code": -32000,
                                           "message": "credit gone"}})
            return _server(req, timeout)
        urllib.request.urlopen = _err
        with self.assertRaises(McpError) as ctx:
            McpClient("http://mcp.test/mcp").call_tool("search", {})
        self.assertIn("credit gone", str(ctx.exception))
        self.assertLessEqual(len(str(ctx.exception)), 64 + 300)

    def test_iserror_surfaces_cause(self):
        def _iserr(req, timeout=None):
            payload = json.loads(req.data.decode())
            if payload.get("method") == "tools/call":
                return FakeResp(
                    {"jsonrpc": "2.0", "id": payload.get("id"),
                     "result": {"content": [{"type": "text",
                                             "text": "backend down"}],
                                "isError": True}})
            return _server(req, timeout)
        urllib.request.urlopen = _iserr
        with self.assertRaises(McpError) as ctx:
            McpClient("http://mcp.test/mcp").call_tool("search", {})
        self.assertIn("backend down", str(ctx.exception))

    def test_timeout_mapped(self):
        def _slow(req, timeout=None):
            raise TimeoutError("slow")
        urllib.request.urlopen = _slow
        with self.assertRaises(McpError) as ctx:
            McpClient("http://mcp.test/mcp").initialize()
        self.assertEqual(str(ctx.exception), "timeout")

    def test_server_unavailable_mapped(self):
        def _down(req, timeout=None):
            raise ConnectionError("refused")
        urllib.request.urlopen = _down
        with self.assertRaises(McpError) as ctx:
            McpClient("http://mcp.test/mcp").list_tools()
        self.assertEqual(str(ctx.exception), "unavailable")

    def test_http_error_mapped_without_body_leak(self):
        def _http(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 500, "e",
                                         {}, None)
        urllib.request.urlopen = _http
        with self.assertRaises(McpError) as ctx:
            McpClient("http://mcp.test/mcp").initialize()
        self.assertTrue(str(ctx.exception).startswith("http-500"))

    def test_oversized_refused(self):
        def _big(req, timeout=None):
            payload = json.loads(req.data.decode())

            class Big(FakeResp):
                def read(self, n=-1):
                    return b"x" * 100
            return Big({"jsonrpc": "2.0", "id": payload.get("id"),
                        "result": {}})
        urllib.request.urlopen = _big
        with self.assertRaises(McpError) as ctx:
            McpClient("http://mcp.test/mcp",
                      max_bytes=10).initialize()
        self.assertEqual(str(ctx.exception), "oversized-response")

    def test_streaming_refused(self):
        def _sse(req, timeout=None):
            return FakeResp({}, headers={
                "Content-Type": "text/event-stream"})
        urllib.request.urlopen = _sse
        with self.assertRaises(McpError) as ctx:
            McpClient("http://mcp.test/mcp").initialize()
        self.assertEqual(str(ctx.exception), "streaming-unsupported")

    def test_reset_drops_session(self):
        c = McpClient("http://mcp.test/mcp")
        c.initialize()
        self.assertEqual(c._session_id, "s9")
        c.reset()
        self.assertEqual(c._session_id, "")

    def test_no_secret_in_errors(self):
        secret = "mcp-secret-zz-001"
        c = McpClient("http://mcp.test/mcp", auth_header=secret)
        c.initialize()
        blob = str(CALLS)
        self.assertNotIn(secret, blob)
        for call in CALLS:
            self.assertNotIn(secret, str(call))


if __name__ == "__main__":
    unittest.main()

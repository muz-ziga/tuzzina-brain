"""Stage B registry tests: semantic capability -> server+tool.
Fully offline: fixtures live in temp YAML files (MCP_SERVERS_FILE
is never required - load_registry takes an explicit path)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mcp.registry import (RegistryError, load_registry, read_auth,
                          resolve)


def _write(doc):
    import yaml
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f)
    return path


def _doc(*servers):
    return {"servers": list(servers)}


def _server(sid="search-a", endpoint="https://s-a.test/mcp",
            tools=None, **over):
    d = {"id": sid, "transport": "http", "endpoint": endpoint,
         "auth_env": "", "enabled": True,
         "tools": tools if tools is not None else [
             {"name": "search", "capability": "web.search"}]}
    d.update(over)
    return d


class LoadCase(unittest.TestCase):
    def test_missing_file_means_zero_servers(self):
        self.assertEqual(
            load_registry("C:\\nonexistent\\mcp-registry.yaml"), [])

    def test_empty_doc_means_zero_servers(self):
        p = _write(None)
        try:
            self.assertEqual(load_registry(p), [])
        finally:
            os.unlink(p)

    def test_valid_doc_loads(self):
        p = _write(_doc(_server()))
        try:
            servers = load_registry(p)
            self.assertEqual(len(servers), 1)
            self.assertEqual(servers[0]["id"], "search-a")
        finally:
            os.unlink(p)

    def test_unknown_top_key_rejected(self):
        p = _write({"servers": [], "mystery": 1})
        try:
            with self.assertRaises(RegistryError):
                load_registry(p)
        finally:
            os.unlink(p)

    def test_unknown_server_key_rejected(self):
        bad = _server()
        bad["vendor"] = "acme"
        p = _write(_doc(bad))
        try:
            with self.assertRaises(RegistryError):
                load_registry(p)
        finally:
            os.unlink(p)

    def test_bad_id_rejected(self):
        for bad_id in ("", "UPPER", "has space", "x" * 65, "a/b"):
            p = _write(_doc(_server(sid=bad_id)))
            try:
                with self.assertRaises(RegistryError):
                    load_registry(p)
            finally:
                os.unlink(p)

    def test_non_http_transport_rejected(self):
        p = _write(_doc(_server(transport="stdio")))
        try:
            with self.assertRaises(RegistryError):
                load_registry(p)
        finally:
            os.unlink(p)

    def test_bad_auth_env_rejected(self):
        p = _write(_doc(_server(auth_env="lowercase")))
        try:
            with self.assertRaises(RegistryError):
                load_registry(p)
        finally:
            os.unlink(p)

    def test_bad_capability_rejected(self):
        bad = _server(tools=[{"name": "search",
                              "capability": "Web Search!"}])
        p = _write(_doc(bad))
        try:
            with self.assertRaises(RegistryError):
                load_registry(p)
        finally:
            os.unlink(p)

    def test_bad_tool_name_rejected(self):
        bad = _server(tools=[{"name": "search now!",
                              "capability": "web.search"}])
        p = _write(_doc(bad))
        try:
            with self.assertRaises(RegistryError):
                load_registry(p)
        finally:
            os.unlink(p)

    def test_bad_rate_limit_rejected(self):
        p = _write(_doc(_server(rate_limit={"per_minute": 0})))
        try:
            with self.assertRaises(RegistryError):
                load_registry(p)
        finally:
            os.unlink(p)

    def test_rate_limit_carried(self):
        p = _write(_doc(_server(rate_limit={"per_minute": 30})))
        try:
            servers = load_registry(p)
            self.assertEqual(servers[0]["rate_limit"],
                             {"per_minute": 30})
        finally:
            os.unlink(p)


class ResolveCase(unittest.TestCase):
    def test_resolve_semantic_name(self):
        servers = [_server(sid="search-a",
                           endpoint="https://s-a.test/mcp")]
        out = resolve("web.search", servers=servers)
        self.assertEqual(out, {"server_id": "search-a",
                               "endpoint": "https://s-a.test/mcp",
                               "auth_env": "", "tool": "search",
                               "rate_limit": {}})

    def test_unknown_capability_fails_closed(self):
        with self.assertRaises(RegistryError):
            resolve("web.search", servers=[])

    def test_invalid_capability_shape_rejected(self):
        with self.assertRaises(RegistryError):
            resolve("Web Search!", servers=[])

    def test_disabled_server_skipped(self):
        servers = [_server(enabled=False)]
        with self.assertRaises(RegistryError):
            resolve("web.search", servers=servers)

    def test_ambiguous_capability_refused(self):
        servers = [_server(sid="a"), _server(sid="b")]
        with self.assertRaises(RegistryError) as ctx:
            resolve("web.search", servers=servers)
        self.assertIn("ambiguous", str(ctx.exception))

    def test_vendor_swap_needs_no_code(self):
        # Company A today, Company B tomorrow: same capability
        # name, different server entry, identical resolve shape.
        a = [_server(sid="company-a",
                     endpoint="https://a.test/mcp")]
        b = [_server(sid="company-b",
                     endpoint="https://b.test/mcp")]
        ra = resolve("web.search", servers=a)
        rb = resolve("web.search", servers=b)
        self.assertNotEqual(ra["server_id"], rb["server_id"])
        self.assertEqual(set(ra), set(rb))


class AuthCase(unittest.TestCase):
    def test_empty_ref_means_anonymous(self):
        self.assertEqual(read_auth(""), "")

    def test_value_never_in_errors(self):
        os.environ["BRAIN_TEST_MCP_KEY"] = "secret-abc-123"
        try:
            self.assertEqual(read_auth("BRAIN_TEST_MCP_KEY"),
                             "secret-abc-123")
            with self.assertRaises(RegistryError):
                resolve("web.search", servers=[])
        finally:
            del os.environ["BRAIN_TEST_MCP_KEY"]

    def test_missing_env_means_empty(self):
        os.environ.pop("BRAIN_TEST_MCP_MISSING", None)
        self.assertEqual(read_auth("BRAIN_TEST_MCP_MISSING"), "")


if __name__ == "__main__":
    unittest.main()

"""Capability registry (Stage B). Maps semantic capability
names to configured MCP servers + tools. File-owned config,
validated strictly (unknown keys rejected, mirroring the
skills loader): the registry never invents servers, tools,
or credentials.

No database (AGENTS.md forbids Brain DB storage). Default
file is src/mcp/servers.yaml; MCP_SERVERS_FILE overrides it
(tests point at fixtures). Secrets NEVER live here: entries
name an environment variable (auth_env); values stay in the
environment and never appear in errors, traces, or state.

Resolution is deterministic and fail-closed: unknown
capability -> no-provider; disabled server/tool skipped;
two enabled providers for one capability -> ambiguous
(refuse rather than guess). Rate-limit policy is validated
and carried for Stage 8 enforcement; this stage performs
no clock-based limiting.
"""
from __future__ import annotations
import os
import re

try:
    import yaml
except Exception:  # pragma: no cover - runtime always has PyYAML
    yaml = None

CAPABILITY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SLUG_RE = re.compile(r"^[a-z0-9-]{1,64}$")
ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class RegistryError(ValueError):
    """Misconfiguration or unresolvable capability. A
    ValueError so misconfiguration exits non-retryable, never
    burning inference budget or touching the network first."""


def _servers_file() -> str:
    override = os.environ.get("MCP_SERVERS_FILE", "").strip()
    if override:
        return override
    return os.path.join(os.path.dirname(__file__), "servers.yaml")


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise RegistryError(msg)


def _check_server(entry, i: int) -> dict:
    _check(isinstance(entry, dict), f"servers[{i}]: must be a mapping")
    for key in entry:
        if key not in ("id", "transport", "endpoint", "auth_env",
                       "enabled", "rate_limit", "tools"):
            raise RegistryError(f"servers[{i}]: unknown key {key!r}")
    sid = entry.get("id", "")
    _check(isinstance(sid, str) and SLUG_RE.match(sid),
           f"servers[{i}].id: must match [a-z0-9-]{{1,64}}")
    transport = entry.get("transport", "http")
    _check(transport == "http",
           f"servers[{i}].transport: only 'http' in Stage B")
    endpoint = entry.get("endpoint", "")
    _check(isinstance(endpoint, str) and endpoint.strip(),
           f"servers[{i}].endpoint: required")
    auth_env = entry.get("auth_env", "")
    _check(auth_env == "" or
           (isinstance(auth_env, str) and ENV_RE.match(auth_env)),
           f"servers[{i}].auth_env: must be an ENV_VAR_NAME or empty")
    enabled = entry.get("enabled", True)
    _check(isinstance(enabled, bool),
           f"servers[{i}].enabled: must be a bool")
    rate_limit = entry.get("rate_limit")
    if rate_limit is not None:
        _check(isinstance(rate_limit, dict),
               f"servers[{i}].rate_limit: must be a mapping")
        for key in rate_limit:
            if key not in ("per_minute",):
                raise RegistryError(
                    f"servers[{i}].rate_limit: unknown key {key!r}")
        if "per_minute" in rate_limit:
            _check(isinstance(rate_limit["per_minute"], int) and
                   rate_limit["per_minute"] >= 1,
                   f"servers[{i}].rate_limit.per_minute: int >= 1")
    tools = entry.get("tools", [])
    _check(isinstance(tools, list),
           f"servers[{i}].tools: must be a list")
    clean_tools = []
    for j, tool in enumerate(tools):
        _check(isinstance(tool, dict),
               f"servers[{i}].tools[{j}]: must be a mapping")
        for key in tool:
            if key not in ("name", "capability"):
                raise RegistryError(
                    f"servers[{i}].tools[{j}]: unknown key {key!r}")
        name = tool.get("name", "")
        _check(isinstance(name, str) and TOOL_NAME_RE.match(name),
               f"servers[{i}].tools[{j}].name: invalid tool name")
        cap = tool.get("capability", "")
        _check(isinstance(cap, str) and CAPABILITY_RE.match(cap),
               f"servers[{i}].tools[{j}].capability: invalid "
               f"capability name")
        clean_tools.append({"name": name, "capability": cap})
    return {"id": sid, "transport": transport,
            "endpoint": endpoint.strip(),
            "auth_env": auth_env, "enabled": enabled,
            "rate_limit": dict(rate_limit or {}),
            "tools": clean_tools}


def load_registry(path: str | None = None) -> list:
    """Load + validate the server list. Missing file means zero
    configured servers (fail-closed at resolve time), never an
    error: an unconfigured Brain simply offers no capabilities."""
    if yaml is None:
        raise RegistryError("pyyaml-unavailable")
    target = path or _servers_file()
    try:
        with open(target, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except FileNotFoundError:
        return []
    except Exception:
        raise RegistryError("unreadable-registry")
    if doc is None:
        return []
    if not isinstance(doc, dict):
        raise RegistryError("registry-must-be-mapping")
    for key in doc:
        if key not in ("servers",):
            raise RegistryError(f"registry: unknown key {key!r}")
    servers = doc.get("servers") or []
    if not isinstance(servers, list):
        raise RegistryError("servers: must be a list")
    return [_check_server(entry, i)
            for i, entry in enumerate(servers)]


def resolve(capability: str, *, servers_file: str | None = None,
            servers: list | None = None) -> dict:
    """capability -> {server_id, endpoint, auth_env, tool,
    rate_limit}. agents request semantic names only. Raises
    RegistryError when no single enabled provider exists."""
    if not isinstance(capability, str) or \
            not CAPABILITY_RE.match(capability):
        raise RegistryError(f"invalid-capability:{capability!r}")
    if servers is None:
        servers = load_registry(servers_file)
    matches = []
    for server in servers:
        if not server.get("enabled", True):
            continue
        for tool in server.get("tools") or []:
            if tool.get("capability") == capability:
                matches.append((server, tool))
    if not matches:
        raise RegistryError(
            f"no-provider-for-capability:{capability}")
    if len(matches) > 1:
        raise RegistryError(
            f"ambiguous-capability:{capability}")
    server, tool = matches[0]
    return {"server_id": server["id"],
            "endpoint": server["endpoint"],
            "auth_env": server.get("auth_env") or "",
            "tool": tool["name"],
            "rate_limit": dict(server.get("rate_limit") or {})}


def read_auth(auth_env: str) -> str:
    """Resolve an auth_env reference to its value. Empty
    reference means anonymous. The VALUE never appears in any
    error raised here or anywhere downstream."""
    if not auth_env:
        return ""
    value = os.environ.get(auth_env, "")
    return value if isinstance(value, str) else ""

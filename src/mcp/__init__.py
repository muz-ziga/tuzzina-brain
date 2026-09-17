"""Generic MCP layer (Stage B foundation).

One reusable JSON-RPC client plus a validated capability
registry. No vendor, company, agent, campaign, or source-type
concepts live here: agents request semantic capabilities
("web.search"), the registry resolves them to a configured
server+tool, and the client invokes exactly that tool.

Secrets never enter this package: callers pass an auth header
value obtained from the environment; values are never logged,
stored, or included in errors.
"""

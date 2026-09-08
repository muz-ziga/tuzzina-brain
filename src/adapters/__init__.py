"""Adapters package: one thin adapter per platform over G2 Core.
Selection is by Tuzzina integration identifier at runtime. Unknown
identifiers raise UnsupportedPlatform (NOT a whitelist failure:
we simply have no adapter yet). No VALID_PLATFORMS list anywhere.
"""
from adapters.base import PlatformAdapter, UnsupportedPlatform
from adapters.facebook import FacebookAdapter
from adapters.instagram import InstagramAdapter


def get_adapter(identifier: str) -> PlatformAdapter:
    ident = (identifier or "").strip().lower()
    if ident == FacebookAdapter.identifier:
        return FacebookAdapter()
    if ident == InstagramAdapter.identifier:
        return InstagramAdapter()
    raise UnsupportedPlatform(
        f"no adapter for Tuzzina identifier {identifier!r}; "
        f"stopping this channel (not a Tuzzina error)")

"""PlatformAdapter interface. One shared G2 Core + one thin adapter
per platform. The adapter NEVER redefines Postiz DTOs and NEVER
validates payload shape (Tuzzina does that). It only translates:
  - shape_text(): assemble generated text + hashtags
  - shape_media(): pass through valid media refs
  - link handling per platform (attach allowed vs ignored)
  - link_setting(): the link URL settings field, if any
  - default_settings(): minimal settings skeleton (identity + kind);
    provider truth itself is fetched live, never stored here
"""
from __future__ import annotations
from abc import ABC, abstractmethod


class UnsupportedPlatform(Exception):
    """Raised when no adapter exists for a Tuzzina identifier.
    NOT a whitelist failure: we simply have no adapter yet."""


class PlatformAdapter(ABC):
    identifier: str = ""

    @abstractmethod
    def shape_text(self, text: str, hashtags: list) -> str:
        """Assemble Core text+hashtags to platform text shape."""

    @abstractmethod
    def shape_media(self, media: list) -> list:
        """Pass through media refs valid for the platform."""

    @abstractmethod
    def apply_link(self, content: str, url: str, policy: str,
                   cta_style: str) -> str:
        """Platform link rule. Some platforms ignore links entirely."""

    @abstractmethod
    def link_setting(self, policy: str, link: str) -> dict:
        """Link URL settings field for the Tuzzina payload, if any."""

    @abstractmethod
    def default_settings(self) -> dict:
        """Minimal settings payload Tuzzina accepts for this platform."""

"""PlatformAdapter interface. One shared G2 Core + one thin adapter
per platform. The adapter NEVER redefines Postiz DTOs and NEVER
validates payload shape (Tuzzina does that). It only supplies:
  - capabilities(): operational limits proven from the pinned
    Postiz source (each value cites file:line @ pinned version)
  - shape_text(): cut/adapt generated text to the platform
  - shape_media(): choose/validate media items for the platform
  - link handling per platform (attach allowed vs ignored)
  - provider_overrides defaults for the platform settings payload
"""
from __future__ import annotations
from abc import ABC, abstractmethod


class UnsupportedPlatform(Exception):
    """Raised when no adapter exists for a Tuzzina identifier.
    NOT a whitelist failure: we simply have no adapter yet."""


class PlatformAdapter(ABC):
    identifier: str = ""

    @abstractmethod
    def capabilities(self) -> dict:
        """Operational limits proven from pinned Postiz source."""

    @abstractmethod
    def shape_text(self, text: str, hashtags: list) -> str:
        """Adapt Core text+hashtags to platform rules (length, placement)."""

    @abstractmethod
    def shape_media(self, media: list) -> list:
        """Select/order media items valid for the platform."""

    @abstractmethod
    def apply_link(self, content: str, url: str, policy: str,
                   cta_style: str) -> str:
        """Platform link rule. Some platforms ignore links entirely."""

    @abstractmethod
    def default_settings(self) -> dict:
        """Minimal settings payload Tuzzina accepts for this platform."""

"""Shared contracts between G1/G2/G3. Plain dataclasses, no dependencies."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Identity:
    name: str = ""
    description: str = ""
    language: str = ""
    logo_url: str = ""
    colors: list = field(default_factory=list)


@dataclass
class SourceItem:
    source_id: str
    source_type: str
    source_url: str
    title: str = ""
    text: str = ""
    images: list = field(default_factory=list)
    links: list = field(default_factory=list)
    hashtags: list = field(default_factory=list)
    published_at: str = ""
    platform: str = ""


@dataclass
class ExtractionResult:
    items: list
    identity: Identity
    meta: dict = field(default_factory=dict)  # {requested, returned, partial, reason}


@dataclass
class ContentPackage:
    title: str
    content: str
    description: str = ""
    hashtags: list = field(default_factory=list)
    links: list = field(default_factory=list)
    media: list = field(default_factory=list)  # [{kind: url|bytes, ...}]
    platform: str = "facebook"
    settings: dict = field(default_factory=dict)
    source_ref: str = ""


@dataclass
class PlannedPost:
    platform: str
    content: str
    media: list
    planned_at: str  # ISO datetime
    settings: dict
    tags: list = field(default_factory=list)

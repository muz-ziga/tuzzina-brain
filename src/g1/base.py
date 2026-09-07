"""SourceAdapter interface. Future: RSSAdapter, FacebookSourceAdapter, ..."""
from __future__ import annotations
from abc import ABC, abstractmethod
from contracts import ExtractionResult


class SourceAdapter(ABC):
    source_type: str = "unknown"

    @abstractmethod
    def extract(self, url: str, n: int) -> ExtractionResult:
        """Extract up to n items. Never throws for partial results;
        returns what exists with meta {requested, returned, partial, reason}."""

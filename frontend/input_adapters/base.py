"""
Abstract base class for all input adapters.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from ..schema import SitePlan


class AbstractAdapter(ABC):
    """Each adapter converts a specific data source to a unified SitePlan."""

    name: str = "base"

    @abstractmethod
    def parse(self, source: Any, **kwargs) -> SitePlan:
        """
        Parse input source and return a SitePlan.

        Args:
            source: The input data (file path, URL, text, etc.)
            **kwargs: Adapter-specific options

        Returns:
            SitePlan with buildings, optional bike stations, and metadata.
        """
        ...

    @abstractmethod
    def validate_source(self, source: Any) -> bool:
        """Check whether this adapter can handle the given source."""
        ...

    def get_metadata(self) -> Dict[str, Any]:
        """Return metadata about this adapter's capabilities."""
        return {
            "adapter": self.name,
            "supported_formats": [],
            "requires_network": False,
            "cost": "free",
        }

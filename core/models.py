from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RenderedImage:
    """A validated image returned by Grafana."""

    content: bytes
    content_type: str

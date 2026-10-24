"""Source adapters. See :mod:`acadfetch.sources.registry` for dispatch."""

from .base import File, Record
from .registry import ADAPTERS, names, resolve

__all__ = ["resolve", "names", "ADAPTERS", "File", "Record"]

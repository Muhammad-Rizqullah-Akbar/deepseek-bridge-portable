"""DeepSeek Bridge Portable (v2.5.0) - Clean Domain-Driven Architecture."""
from __future__ import annotations

__version__ = "2.5.0"

# Backward-compatibility alias re-exports
from .core import config as config
from .browser import auth as auth
from .browser import browse as browse
from .state import registry as accounts

__all__ = ["__version__", "config", "auth", "browse", "accounts"]

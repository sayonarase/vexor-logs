"""Convenience entry point for plugin loaders that expect ``router.py``.

Re-exports the main logs router. The previous version had `from . import routers`
which referenced a non-existent submodule.
"""
from .logs_router import router  # noqa: F401   # default "router" symbol

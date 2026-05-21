"""Convenience entry point for plugin loaders that expect ``router.py``."""
from . import routers  # noqa: F401
from .logs_router import router  # noqa: F401   # default "router" symbol

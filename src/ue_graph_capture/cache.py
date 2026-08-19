from __future__ import annotations

import os
import sys
from pathlib import Path


def user_cache_root() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA")
        return Path(base) if base else Path.home() / "AppData" / "Local"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))


def graphprinter_cache_root() -> Path:
    return user_cache_root() / "ue-graph-capture" / "GraphPrinter"

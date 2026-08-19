from __future__ import annotations

import sys

from .base import EditorCandidate


def current_adapter():
    if sys.platform.startswith("win"):
        from . import windows

        return windows
    if sys.platform == "darwin":
        from . import macos

        return macos
    from . import linux

    return linux


__all__ = ["EditorCandidate", "current_adapter"]

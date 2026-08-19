from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

REQUEST_VERSION = 1


@dataclass(frozen=True)
class CaptureRequest:
    """One immutable request exchanged with the Unreal Editor plugin."""

    action: str
    project: Path
    asset: str
    graph: str | None
    output_directory: Path
    result_path: Path
    scale: float = 1.0
    padding: int = 100
    draw_only_graph: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": REQUEST_VERSION,
            "action": self.action,
            "project": str(self.project),
            "asset": self.asset,
            "graph": self.graph,
            "outputDirectory": str(self.output_directory),
            "resultPath": str(self.result_path),
            "scale": self.scale,
            "padding": self.padding,
            "drawOnlyGraph": self.draw_only_graph,
        }


def result_error(result: dict[str, Any]) -> str:
    message = result.get("error")
    if isinstance(message, str) and message:
        return message
    return "Unreal Editor capture plugin returned an unsuccessful result."

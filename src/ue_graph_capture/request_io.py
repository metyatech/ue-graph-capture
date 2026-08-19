from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import CaptureError
from .models import CaptureRequest


def write_request(path: Path, request: CaptureRequest) -> None:
    path.write_text(
        json.dumps(request.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_result(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise CaptureError(f"Unreal Editor exited without writing result metadata: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaptureError(f"Invalid Unreal Editor result JSON {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CaptureError(f"Unreal Editor result must be a JSON object: {path}")
    return data

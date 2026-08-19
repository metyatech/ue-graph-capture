from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .editor import read_unreal_version, resolve_unreal_editor
from .errors import CaptureError
from .project import resolve_project_path
from .setup_project import GRAPHPRINTER_REVISION, GRAPHPRINTER_VERSION


def _plugin_status(project: Path, name: str) -> dict[str, Any]:
    descriptor = project.parent / "Plugins" / name / f"{name}.uplugin"
    status: dict[str, Any] = {"installed": descriptor.is_file(), "path": str(descriptor)}
    if descriptor.is_file():
        try:
            data = json.loads(descriptor.read_text(encoding="utf-8"))
            status["version"] = data.get("VersionName", data.get("Version"))
        except (OSError, json.JSONDecodeError):
            status["version"] = "invalid descriptor"
    return status


def run_doctor(project_raw: str, editor_raw: str | None) -> dict[str, Any]:
    checks: dict[str, Any] = {
        "python": {"ok": sys.version_info >= (3, 10), "version": sys.version.split()[0]},
    }
    try:
        project = resolve_project_path(project_raw)
    except CaptureError as exc:
        checks["project"] = {"ok": False, "error": str(exc)}
        return {"ok": False, "checks": checks}
    checks["project"] = {"ok": True, "path": str(project)}

    try:
        editor = resolve_unreal_editor(project, editor_raw)
        version = read_unreal_version(editor)
        checks["unrealEditor"] = {"ok": True, "path": str(editor), "version": version}
    except CaptureError as exc:
        checks["unrealEditor"] = {"ok": False, "error": str(exc)}

    checks["graphPrinter"] = _plugin_status(project, "GraphPrinter")
    checks["graphPrinter"]["expectedRevision"] = GRAPHPRINTER_REVISION
    checks["graphPrinter"]["expectedVersion"] = GRAPHPRINTER_VERSION
    checks["ueGraphCapture"] = _plugin_status(project, "UEGraphCapture")
    checks["ueGraphCapture"]["expectedVersion"] = "0.1.0"
    all_ok = all(
        isinstance(value, dict) and value.get("ok", value.get("installed", False))
        for value in checks.values()
    )
    return {"ok": all_ok, "checks": checks}

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .errors import CaptureError
from .platforms import current_adapter
from .project import engine_association


def _override_path(raw_path: str | Path) -> Path:
    path = Path(raw_path).expanduser().resolve(strict=False)
    if path.is_dir():
        path = current_adapter().editor_from_install_dir(path)
    return path


def resolve_unreal_editor(project: Path, explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        path = _override_path(explicit)
        if path.is_file():
            return path
        raise CaptureError(f"The specified Unreal Editor was not found: {path}")

    association = engine_association(project)
    candidates = current_adapter().editor_candidates(association)
    for candidate in candidates:
        if candidate.path.is_file():
            return candidate.path
    searched = ", ".join(str(candidate.path) for candidate in candidates[:8]) or "no candidates"
    raise CaptureError(
        f"Could not resolve Unreal Editor for EngineAssociation {association!r}; "
        f"searched {searched}. "
        "Pass --unreal-editor <path>."
    )


def read_unreal_version(editor: Path, timeout: float = 15.0) -> str:
    version_file = editor.parent / "UnrealEditor.version"
    if version_file.is_file():
        try:
            data = json.loads(version_file.read_text(encoding="utf-8"))
            major = int(data["MajorVersion"])
            minor = int(data["MinorVersion"])
            patch = int(data.get("PatchVersion", 0))
            return f"{major}.{minor}.{patch}"
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CaptureError(
                f"Unreal Editor version metadata is invalid: {version_file}: {exc}"
            ) from exc
    try:
        completed = subprocess.run(
            [str(editor), "-version"],
            cwd=editor.parent,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            shell=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CaptureError(f"Unable to query Unreal Editor version at {editor}: {exc}") from exc
    output = f"{completed.stdout}\n{completed.stderr}"
    match = re.search(
        r"(?:engine version|ue version|version)\s*[:=]?\s*(\d+\.\d+(?:\.\d+)?)", output, re.I
    )
    if match:
        return match.group(1)
    match = re.search(r"\b(5\.\d+(?:\.\d+)?)\b", output)
    if match:
        return match.group(1)
    raise CaptureError(
        f"Unreal Editor did not report a recognizable version: {output.strip()[:500]}"
    )

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import CaptureError
from .validation import validate_asset_path


@dataclass(frozen=True)
class FileSnapshot:
    relative_path: str
    size: int
    mtime_ns: int
    sha256: str


def resolve_project_path(raw_path: str | Path) -> Path:
    path = Path(raw_path).expanduser().resolve(strict=False)
    if path.suffix.lower() != ".uproject":
        raise CaptureError(f"Project must be a .uproject file: {path}")
    if not path.is_file():
        raise CaptureError(f"Project file was not found: {path}")
    return path


def load_uproject(project: Path) -> dict[str, Any]:
    try:
        with project.open(encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise CaptureError(f"Unable to read project descriptor {project}: {exc}") from exc
    if not isinstance(data, dict):
        raise CaptureError(f"Project descriptor must contain a JSON object: {project}")
    return data


def engine_association(project: Path) -> str:
    value = load_uproject(project).get("EngineAssociation")
    if not isinstance(value, str) or not value:
        raise CaptureError(f"Project has no EngineAssociation: {project}")
    return value


def write_json_atomic(path: Path, data: Any) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def update_plugin_enablement(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    plugins = data.get("Plugins")
    if plugins is None:
        plugins = []
    if not isinstance(plugins, list):
        raise CaptureError("Project Plugins field must be an array when present.")

    changed = False
    result = dict(data)
    result["Plugins"] = plugins
    for name in ("UEGraphCapture", "GraphPrinter"):
        entry = next(
            (item for item in plugins if isinstance(item, dict) and item.get("Name") == name),
            None,
        )
        if entry is None:
            plugins.append({"Name": name, "Enabled": True})
            changed = True
        elif entry.get("Enabled") is not True:
            entry["Enabled"] = True
            changed = True
    return result, changed


def enable_plugins(project: Path) -> bool:
    data = load_uproject(project)
    updated, changed = update_plugin_enablement(data)
    if changed:
        write_json_atomic(project, updated)
    return changed


def _asset_candidate_paths(project: Path, asset: str) -> list[Path]:
    package = validate_asset_path(asset)[len("/Game/") :]
    relative = Path(*package.split("/"))
    content_root = project.parent / "Content"
    return [content_root / relative.with_suffix(extension) for extension in (".uasset", ".umap")]


def _iter_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    if root.is_file():
        return [root]
    return [path for path in root.rglob("*") if path.is_file()]


def _file_digest(path: Path) -> str:
    from .validation import sha256_file

    return sha256_file(path)


def snapshot_project(project: Path, asset: str) -> tuple[FileSnapshot, ...]:
    """Capture only source-facing files that capture is forbidden to mutate."""

    roots = [project, project.parent / "Config", *_asset_candidate_paths(project, asset)]
    files: dict[str, Path] = {}
    for root in roots:
        for path in _iter_files(root):
            try:
                relative = path.resolve().relative_to(project.parent.resolve()).as_posix()
            except ValueError as exc:
                raise CaptureError(f"Project snapshot escaped project root: {path}") from exc
            files[relative] = path
    return tuple(
        FileSnapshot(
            relative_path=relative,
            size=files[relative].stat().st_size,
            mtime_ns=files[relative].stat().st_mtime_ns,
            sha256=_file_digest(files[relative]),
        )
        for relative in sorted(files)
    )

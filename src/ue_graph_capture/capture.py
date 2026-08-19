from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from .editor import resolve_unreal_editor
from .errors import CaptureError
from .models import CaptureRequest
from .process import run_editor_request
from .project import snapshot_project
from .request_io import read_result, write_request
from .setup_project import GRAPHPRINTER_REVISION, GRAPHPRINTER_VERSION
from .validation import (
    safe_output_filename,
    validate_asset_path,
    validate_graph_name,
    validate_output_path,
    validate_png,
)


def _plugin_descriptor(project: Path, plugin_name: str) -> Path:
    return project.parent / "Plugins" / plugin_name / f"{plugin_name}.uplugin"


def _assert_plugins_installed(project: Path) -> None:
    missing = [
        name
        for name in ("UEGraphCapture", "GraphPrinter")
        if not _plugin_descriptor(project, name).is_file()
    ]
    if missing:
        raise CaptureError(
            "Required project plugins are missing: "
            + ", ".join(missing)
            + ". Run `ue-graph-capture setup --project <uproject>` first."
        )


def _inside(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
    except ValueError:
        return False
    return True


def _single_staged_png(directory: Path, result: dict[str, Any]) -> Path:
    candidates = sorted(directory.glob("*.png"))
    result_path = result.get("output")
    if isinstance(result_path, str):
        candidate = Path(result_path).resolve()
        if not _inside(candidate, directory):
            raise CaptureError("Unreal Editor result points outside its staging directory.")
        if candidate not in candidates:
            candidates.append(candidate)
    candidates = sorted({candidate.resolve() for candidate in candidates})
    if len(candidates) != 1:
        names = ", ".join(str(path) for path in candidates) or "none"
        raise CaptureError(f"Expected exactly one staged PNG, found {len(candidates)}: {names}")
    return candidates[0]


def _publish_png(staged: Path, output: Path) -> Path:
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.capture.tmp")
    try:
        shutil.copy2(staged, temporary)
        temporary.replace(output)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise CaptureError(f"Unable to publish PNG to {output}: {exc}") from exc
    return output


def _run_request(
    *,
    project: Path,
    request: CaptureRequest,
    editor: str | Path | None,
    timeout: float,
) -> dict[str, Any]:
    editor_path = resolve_unreal_editor(project, editor)
    request_path = request.result_path.parent / "request.json"
    write_request(request_path, request)
    process_result = run_editor_request(editor_path, project, request_path, timeout)
    result = read_result(request.result_path)
    if result.get("ok") is not True:
        detail = result.get("error", "unknown plugin error")
        if process_result.returncode != 0:
            detail = f"{detail} (Unreal Editor exit code {process_result.returncode})"
        raise CaptureError(str(detail), details=result)
    if process_result.returncode != 0:
        raise CaptureError(
            f"Unreal Editor exited with code {process_result.returncode} after reporting success."
        )
    return result


def capture_graph(
    *,
    project: Path,
    asset: str,
    graph: str,
    output: str | Path | None,
    editor: str | Path | None,
    scale: float,
    padding: int,
    timeout: float,
) -> dict[str, Any]:
    asset = validate_asset_path(asset)
    graph = validate_graph_name(graph)
    final_output = validate_output_path(
        project,
        Path(output) if output is not None else Path.cwd() / safe_output_filename(asset, graph),
    )
    _assert_plugins_installed(project)
    before = snapshot_project(project, asset)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="ue-graph-capture-") as temporary_root:
        temporary_root_path = Path(temporary_root)
        staged_output = temporary_root_path / "output"
        staged_output.mkdir()
        result_path = temporary_root_path / "result.json"
        request = CaptureRequest(
            action="capture",
            project=project,
            asset=asset,
            graph=graph,
            output_directory=staged_output,
            result_path=result_path,
            scale=scale,
            padding=padding,
        )
        result = _run_request(
            project=project,
            request=request,
            editor=editor,
            timeout=timeout,
        )
        staged_png = _single_staged_png(staged_output, result)
        validate_png(staged_png)
        final_output = validate_output_path(project, final_output)
        published = _publish_png(staged_png, final_output)
        after = snapshot_project(project, asset)
        if before != after:
            raise CaptureError(
                "Capture changed a source-facing project file "
                "(.uproject, Config, or target Content asset)."
            )

    published_image = validate_png(published)
    duration_ms = round((time.monotonic() - started) * 1000)
    return {
        "project": str(project),
        "asset": asset,
        "graph": graph,
        "graphType": result.get("graphType", "unknown"),
        "output": str(published),
        "width": published_image["width"],
        "height": published_image["height"],
        "fileSize": published_image["fileSize"],
        "sha256": published_image["sha256"],
        "unrealVersion": result.get("unrealVersion", "unknown"),
        "graphPrinterVersion": result.get("graphPrinterVersion", GRAPHPRINTER_VERSION),
        "graphPrinterRevision": result.get("graphPrinterRevision", GRAPHPRINTER_REVISION),
        "durationMs": duration_ms,
        "captureMethod": "GraphPrinter direct public API",
        "drawOnlyGraph": True,
        "scale": scale,
        "padding": padding,
    }


def list_graphs(
    *, project: Path, asset: str, editor: str | Path | None, timeout: float
) -> list[dict[str, Any]]:
    asset = validate_asset_path(asset)
    _assert_plugins_installed(project)
    with tempfile.TemporaryDirectory(prefix="ue-graph-list-") as temporary_root:
        root = Path(temporary_root)
        request = CaptureRequest(
            action="listGraphs",
            project=project,
            asset=asset,
            graph=None,
            output_directory=root / "output",
            result_path=root / "result.json",
        )
        request.output_directory.mkdir()
        result = _run_request(
            project=project,
            request=request,
            editor=editor,
            timeout=timeout,
        )
        graphs = result.get("graphs")
        if not isinstance(graphs, list):
            raise CaptureError("Unreal Editor returned no graph list.")
        return [item for item in graphs if isinstance(item, dict)]

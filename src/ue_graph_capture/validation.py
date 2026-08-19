from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat

from .errors import CaptureError

_ASSET_PATTERN = re.compile(r"^/Game(?:/[^/]+)+$")
_SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")
_MIN_PNG_SIZE = 1024
_MAX_PNG_SIZE = 2 * 1024 * 1024 * 1024


def validate_asset_path(asset: str) -> str:
    if not isinstance(asset, str) or not _ASSET_PATTERN.fullmatch(asset):
        raise CaptureError("Asset path must be a package path such as /Game/Blueprints/BP_Player.")
    if ".." in asset or "\\" in asset or ":" in asset:
        raise CaptureError("Asset path must not contain traversal, backslashes, or drive syntax.")
    return asset


def validate_graph_name(graph: str) -> str:
    if not graph or not graph.strip() or "/" in graph or "\\" in graph:
        raise CaptureError("Graph name must be a non-empty graph name, not a path.")
    return graph


def safe_output_filename(asset: str, graph: str) -> str:
    asset_name = asset.rstrip("/").rsplit("/", 1)[-1]
    safe_asset = _SAFE_NAME_PATTERN.sub("_", asset_name).strip("._") or "Blueprint"
    safe_graph = _SAFE_NAME_PATTERN.sub("_", graph).strip("._") or "Graph"
    return f"{safe_asset}_{safe_graph}.png"


def validate_output_path(project: Path, output: Path) -> Path:
    """Validate and resolve a capture destination without touching the filesystem."""

    project = project.expanduser().resolve(strict=False)
    output = output.expanduser().resolve(strict=False)
    if output == project:
        raise CaptureError(f"Capture output must not overwrite the project descriptor: {output}")
    if output.suffix.lower() != ".png":
        raise CaptureError(f"Capture output must use the .png extension: {output}")

    def is_within(directory: Path) -> bool:
        try:
            output.relative_to(directory)
        except ValueError:
            return False
        return True

    protected_directories = tuple(
        project.parent / name for name in ("Content", "Config", "Plugins")
    )
    if any(is_within(directory.resolve(strict=False)) for directory in protected_directories):
        raise CaptureError(
            "Capture output must be outside the project's Content, Config, and Plugins trees: "
            f"{output}"
        )
    return output


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_png(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise CaptureError(f"PNG output was not found: {path}")
    size = path.stat().st_size
    if size < _MIN_PNG_SIZE:
        raise CaptureError(f"PNG output is suspiciously small ({size} bytes): {path}")
    if size > _MAX_PNG_SIZE:
        raise CaptureError(f"PNG output is unreasonably large ({size} bytes): {path}")

    try:
        with Image.open(path) as image:
            if image.format != "PNG":
                raise CaptureError(f"Output is not a PNG image: {path}")
            image.verify()
        with Image.open(path) as image:
            image.load()
            if image.width <= 0 or image.height <= 0:
                raise CaptureError("PNG dimensions must be positive.")
            extrema = ImageStat.Stat(image.convert("RGB")).extrema
            if all(low == high for low, high in extrema):
                raise CaptureError("PNG is a single-color image and is not a graph capture.")
            width = image.width
            height = image.height
    except CaptureError:
        raise
    except Exception as exc:  # Pillow exposes several concrete decode exceptions.
        raise CaptureError(f"PNG validation failed for {path}: {exc}") from exc

    return {
        "width": width,
        "height": height,
        "fileSize": size,
        "sha256": sha256_file(path),
    }

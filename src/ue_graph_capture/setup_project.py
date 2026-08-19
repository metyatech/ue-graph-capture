from __future__ import annotations

import json
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from .cache import graphprinter_cache_root
from .editor import resolve_unreal_editor
from .errors import CaptureError
from .platforms import current_adapter
from .project import enable_plugins, load_uproject, update_plugin_enablement, write_json_atomic

GRAPHPRINTER_URL = "https://github.com/Naotsun19B/GraphPrinter.git"
GRAPHPRINTER_REVISION = "9c42bba098926c2066cf52877909d9b3ccd26d9f"
GRAPHPRINTER_VERSION = "3.2"
UE_PLUGIN_NAME = "UEGraphCapture"
GRAPHPRINTER_PLUGIN_NAME = "GraphPrinter"
PLUGIN_MARKER = ".ue-graph-capture-managed.json"
UE_PLUGIN_VERSION = "0.1.0"


@dataclass(frozen=True)
class SetupPlan:
    project: Path
    graphprinter_destination: Path
    ue_plugin_destination: Path
    enablement_change: bool
    graphprinter_change: bool
    graphprinter_license_change: bool
    ue_plugin_change: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": str(self.project),
            "changes": {
                "graphPrinter": self.graphprinter_change,
                "graphPrinterLicense": self.graphprinter_license_change,
                "ueGraphCapture": self.ue_plugin_change,
                "uprojectPluginEnablement": self.enablement_change,
            },
            "graphPrinterDestination": str(self.graphprinter_destination),
            "ueGraphCaptureDestination": str(self.ue_plugin_destination),
            "graphPrinterRevision": GRAPHPRINTER_REVISION,
        }


def _run_checked(
    command: list[str], *, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            shell=False,
        )
    except OSError as exc:
        raise CaptureError(f"Unable to run {' '.join(command)}: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-1000:]
        raise CaptureError(
            f"Command failed with exit code {completed.returncode}: {' '.join(command)}\n{detail}"
        )
    return completed


def _read_marker(plugin_path: Path) -> dict[str, Any] | None:
    marker = plugin_path / PLUGIN_MARKER
    if not marker.is_file():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaptureError(f"Managed plugin marker is invalid: {marker}: {exc}") from exc
    return data if isinstance(data, dict) else None


def _plugin_state(destination: Path, plugin_name: str) -> tuple[bool, bool]:
    if not destination.exists():
        return False, False
    descriptor = destination / f"{plugin_name}.uplugin"
    if not descriptor.is_file():
        raise CaptureError(
            f"A non-plugin directory already exists at {destination}; setup will not overwrite it."
        )
    marker = _read_marker(destination)
    if marker is None:
        raise CaptureError(
            f"An unmanaged {plugin_name} plugin already exists at {destination}; "
            "setup will not overwrite an existing plugin."
        )
    if plugin_name == GRAPHPRINTER_PLUGIN_NAME:
        managed = marker.get("revision") == GRAPHPRINTER_REVISION
    else:
        managed = marker.get("version") == UE_PLUGIN_VERSION
    return True, managed


def _normalized_repository_url(value: str) -> str:
    return value.strip().rstrip("/").removesuffix(".git").casefold()


def _remove_directory_safely(directory: Path) -> None:
    if not directory.is_dir() or directory.is_symlink():
        raise CaptureError(f"Expected a managed directory for removal: {directory}")
    for child in (directory, *directory.rglob("*")):
        if child.is_symlink():
            continue
        try:
            child.chmod(child.stat().st_mode | stat.S_IWRITE)
        except OSError as exc:
            raise CaptureError(
                f"Unable to normalize managed directory attributes: {child}"
            ) from exc
    shutil.rmtree(directory)
    if directory.exists() or directory.is_symlink():
        raise CaptureError(f"Unable to remove managed directory: {directory}")


def _graphprinter_origin_matches(repository: Path) -> bool:
    try:
        origin = _run_checked(["git", "remote", "get-url", "origin"], cwd=repository)
    except CaptureError:
        return False
    return _normalized_repository_url(origin.stdout) == _normalized_repository_url(GRAPHPRINTER_URL)


def _remove_managed_graphprinter_source(cache_root: Path, repository: Path) -> None:
    resolved_root = cache_root.resolve(strict=False)
    resolved_repository = repository.resolve(strict=False)
    if resolved_repository == resolved_root:
        raise CaptureError("Refusing to remove the GraphPrinter cache root itself.")
    try:
        resolved_repository.relative_to(resolved_root)
    except ValueError as exc:
        raise CaptureError(
            f"Refusing to remove a GraphPrinter cache path outside {resolved_root}: "
            f"{resolved_repository}"
        ) from exc
    if repository.is_dir() and not repository.is_symlink():
        _remove_directory_safely(repository)
    elif repository.exists() or repository.is_symlink():
        repository.unlink()
    if repository.exists() or repository.is_symlink():
        raise CaptureError(f"Unable to remove stale GraphPrinter cache: {repository}")


def _ensure_graphprinter_source(*, allow_download: bool) -> Path:
    cache_root = graphprinter_cache_root()
    repository = cache_root / "source"
    plugin = repository / "Plugins" / "GraphPrinter"
    if not allow_download:
        return plugin
    cache_root.mkdir(parents=True, exist_ok=True)
    if not (repository / ".git").exists():
        if repository.exists():
            _remove_managed_graphprinter_source(cache_root, repository)
        _run_checked(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                GRAPHPRINTER_URL,
                str(repository),
            ]
        )
    elif not _graphprinter_origin_matches(repository):
        _remove_managed_graphprinter_source(cache_root, repository)
        _run_checked(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                GRAPHPRINTER_URL,
                str(repository),
            ]
        )

    if not _graphprinter_origin_matches(repository):
        raise CaptureError(
            "GraphPrinter cache origin does not match the expected upstream repository: "
            f"{GRAPHPRINTER_URL}"
        )
    _run_checked(["git", "fetch", "--depth", "1", "origin", GRAPHPRINTER_REVISION], cwd=repository)
    _run_checked(["git", "reset", "--hard", GRAPHPRINTER_REVISION], cwd=repository)
    _run_checked(["git", "clean", "-fdx"], cwd=repository)
    head = _run_checked(["git", "rev-parse", "HEAD"], cwd=repository).stdout.strip()
    status = _run_checked(["git", "status", "--porcelain"], cwd=repository).stdout.strip()
    if head != GRAPHPRINTER_REVISION or status:
        raise CaptureError(
            "GraphPrinter cache is not pinned to a clean requested revision: "
            f"HEAD={head!r}, status={status!r}"
        )
    if not (plugin / "GraphPrinter.uplugin").is_file():
        raise CaptureError(f"Pinned GraphPrinter revision has no plugin package: {plugin}")
    if not (repository / "LICENSE").is_file():
        raise CaptureError(f"Pinned GraphPrinter revision has no root LICENSE: {repository}")
    return plugin


def _find_plugin_directory(root: Path, plugin_name: str) -> Path:
    matches = sorted(root.rglob(f"{plugin_name}.uplugin"))
    if len(matches) != 1:
        raise CaptureError(
            f"GraphPrinter packaging produced {len(matches)} {plugin_name}.uplugin files; "
            "expected exactly one."
        )
    return matches[0].parent


def _bundled_ue_plugin_resource() -> Any:
    resource = resources.files("ue_graph_capture").joinpath("bundled", UE_PLUGIN_NAME)
    if not resource.is_dir():
        raise CaptureError(
            "The bundled UEGraphCapture plugin resource is missing from the package."
        )
    return resource


def _copy_resource_tree(source: Any, destination: Path) -> None:
    if source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        for child in source.iterdir():
            _copy_resource_tree(child, destination / child.name)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source.open("rb") as input_stream, destination.open("wb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream)
    except OSError as exc:
        raise CaptureError(
            f"Unable to copy bundled UEGraphCapture resource {source}: {exc}"
        ) from exc


def _install_bundled_ue_plugin(destination: Path) -> bool:
    stage_root = Path(tempfile.mkdtemp(prefix="ue-graph-capture-bundled-plugin-"))
    staged = stage_root / UE_PLUGIN_NAME
    try:
        _copy_resource_tree(_bundled_ue_plugin_resource(), staged)
        return _install_plugin(
            staged,
            destination,
            UE_PLUGIN_NAME,
            {"version": UE_PLUGIN_VERSION, "source": "ue-graph-capture"},
        )
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)


def _ensure_graphprinter_package(source: Path, editor: Path) -> Path:
    package_cache = graphprinter_cache_root() / f"package-{GRAPHPRINTER_REVISION}"
    cached_plugin = package_cache / "GraphPrinter.uplugin"
    cached_marker = package_cache / PLUGIN_MARKER
    cached_license = package_cache / "LICENSE"
    if cached_plugin.is_file() and cached_marker.is_file() and cached_license.is_file():
        return package_cache

    source_license = source.parent.parent / "LICENSE"
    if not source_license.is_file():
        raise CaptureError(f"GraphPrinter source LICENSE was not found: {source_license}")
    uat = current_adapter().run_uat_from_editor(editor)
    if not uat.is_file():
        raise CaptureError(f"Unreal AutomationTool was not found: {uat}")
    build_root = Path(tempfile.mkdtemp(prefix="ue-graph-capture-graphprinter-build-"))
    package_output = build_root / "Package"
    try:
        _run_checked(
            [
                str(uat),
                "-NoP4",
                "BuildPlugin",
                f"-Plugin={source / 'GraphPrinter.uplugin'}",
                f"-Package={package_output}",
                "-CreateSubFolder",
                "-Rocket",
            ],
            cwd=editor.parent,
        )
        packaged_plugin = _find_plugin_directory(package_output, "GraphPrinter")
        package_cache.parent.mkdir(parents=True, exist_ok=True)
        if package_cache.exists():
            shutil.rmtree(package_cache)
        shutil.copytree(packaged_plugin, package_cache)
        shutil.copy2(source_license, package_cache / "LICENSE")
        write_json_atomic(
            package_cache / PLUGIN_MARKER,
            {
                "source": GRAPHPRINTER_URL,
                "revision": GRAPHPRINTER_REVISION,
                "version": GRAPHPRINTER_VERSION,
                "builtBy": "RunUAT BuildPlugin",
            },
        )
        return package_cache
    finally:
        shutil.rmtree(build_root, ignore_errors=True)


def _copy_graphprinter_license(package: Path, destination: Path) -> None:
    source = package / "LICENSE"
    if not source.is_file():
        raise CaptureError(f"GraphPrinter package LICENSE was not found: {source}")
    target = destination / "LICENSE"
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        shutil.copy2(source, temporary)
        temporary.replace(target)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise CaptureError(f"Unable to install GraphPrinter LICENSE at {target}: {exc}") from exc


def _stage_plugin(source: Path, destination: Path, marker: dict[str, Any]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage_root = Path(tempfile.mkdtemp(prefix="ue-graph-capture-plugin-"))
    staged = stage_root / destination.name
    try:
        shutil.copytree(source, staged)
        write_json_atomic(staged / PLUGIN_MARKER, marker)
        if destination.exists():
            raise CaptureError(f"Plugin destination appeared during setup: {destination}")
        shutil.move(str(staged), str(destination))
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)


def _install_plugin(
    source: Path, destination: Path, plugin_name: str, marker: dict[str, Any]
) -> bool:
    exists, managed = _plugin_state(destination, plugin_name)
    if exists and managed:
        return False
    if exists:
        raise CaptureError(
            f"Existing {plugin_name} plugin is not the requested pinned installation."
        )
    _stage_plugin(source, destination, marker)
    return True


def _plugin_has_binaries(plugin_path: Path) -> bool:
    binaries = plugin_path / "Binaries"
    return binaries.is_dir() and any(path.is_file() for path in binaries.rglob("*"))


def _ue_plugin_needs_build(destination: Path) -> bool:
    if not destination.is_dir():
        return False
    marker = _read_marker(destination)
    return not (
        marker is not None
        and marker.get("version") == UE_PLUGIN_VERSION
        and marker.get("built") is True
        and _plugin_has_binaries(destination)
    )


def _build_project_plugin(project: Path, editor: Path, plugin: Path) -> None:
    try:
        ubt_runtime = current_adapter().ubt_command_from_editor(editor)
        platform_name = current_adapter().ubt_platform_name()
    except (AttributeError, FileNotFoundError) as exc:
        raise CaptureError(str(exc)) from exc

    build_root = Path(tempfile.mkdtemp(prefix="ue-graph-capture-ubt-"))
    manifest = build_root / "manifest.json"
    log = build_root / "ubt.log"
    command = [
        *(str(path) for path in ubt_runtime),
        "UnrealEditor",
        platform_name,
        "Development",
        f"-Project={project}",
        f"-plugin={plugin / (plugin.name + '.uplugin')}",
        "-noubtmakefiles",
        f"-manifest={manifest}",
        "-nohotreload",
        f"-log={log}",
    ]
    try:
        _run_checked(command, cwd=editor.parent)
        if not _plugin_has_binaries(plugin):
            raise CaptureError(
                f"UnrealBuildTool completed without producing plugin binaries: {plugin}"
            )
        marker = _read_marker(plugin) or {}
        marker.update({"version": UE_PLUGIN_VERSION, "built": True, "target": "UnrealEditor"})
        write_json_atomic(plugin / PLUGIN_MARKER, marker)
    finally:
        shutil.rmtree(build_root, ignore_errors=True)


def build_setup_plan(project: Path) -> SetupPlan:
    project = project.resolve()
    project_plugins = project.parent / "Plugins"
    graphprinter_destination = project_plugins / GRAPHPRINTER_PLUGIN_NAME
    ue_destination = project_plugins / UE_PLUGIN_NAME
    graphprinter_exists, graphprinter_managed = _plugin_state(
        graphprinter_destination, GRAPHPRINTER_PLUGIN_NAME
    )
    ue_exists, ue_managed = _plugin_state(ue_destination, UE_PLUGIN_NAME)
    data = load_uproject(project)
    _, enablement_change = update_plugin_enablement(data)
    return SetupPlan(
        project=project,
        graphprinter_destination=graphprinter_destination,
        ue_plugin_destination=ue_destination,
        enablement_change=enablement_change,
        graphprinter_change=not (graphprinter_exists and graphprinter_managed),
        graphprinter_license_change=(
            graphprinter_exists
            and graphprinter_managed
            and not (graphprinter_destination / "LICENSE").is_file()
        ),
        ue_plugin_change=not (ue_exists and ue_managed),
    )


def setup_project(
    project: Path, *, dry_run: bool = False, editor: str | Path | None = None
) -> SetupPlan:
    plan = build_setup_plan(project)
    if dry_run:
        return plan

    created: list[Path] = []
    original_uproject = project.read_bytes()
    needs_ue_build = plan.ue_plugin_change or _ue_plugin_needs_build(plan.ue_plugin_destination)
    graphprinter_license_path = plan.graphprinter_destination / "LICENSE"
    original_graphprinter_license = (
        graphprinter_license_path.read_bytes()
        if plan.graphprinter_license_change and graphprinter_license_path.is_file()
        else None
    )
    editor_path: Path | None = None
    try:
        if plan.graphprinter_change or plan.graphprinter_license_change or needs_ue_build:
            editor_path = resolve_unreal_editor(project, editor)
        graphprinter_source = (
            _ensure_graphprinter_source(allow_download=True)
            if plan.graphprinter_change or plan.graphprinter_license_change
            else None
        )
        if plan.graphprinter_change:
            assert graphprinter_source is not None
            assert editor_path is not None
            graphprinter_package = _ensure_graphprinter_package(graphprinter_source, editor_path)
            _install_plugin(
                graphprinter_package,
                plan.graphprinter_destination,
                GRAPHPRINTER_PLUGIN_NAME,
                {
                    "source": GRAPHPRINTER_URL,
                    "revision": GRAPHPRINTER_REVISION,
                    "version": GRAPHPRINTER_VERSION,
                },
            )
            created.append(plan.graphprinter_destination)
        elif plan.graphprinter_license_change:
            assert graphprinter_source is not None
            assert editor_path is not None
            graphprinter_package = _ensure_graphprinter_package(graphprinter_source, editor_path)
            _copy_graphprinter_license(graphprinter_package, plan.graphprinter_destination)
        if plan.ue_plugin_change:
            if _install_bundled_ue_plugin(plan.ue_plugin_destination):
                created.append(plan.ue_plugin_destination)
        enable_plugins(project)
        if needs_ue_build:
            assert editor_path is not None
            _build_project_plugin(project, editor_path, plan.ue_plugin_destination)
    except Exception:
        project.write_bytes(original_uproject)
        for path in reversed(created):
            if path.exists():
                _remove_directory_safely(path)
        if plan.graphprinter_license_change:
            if original_graphprinter_license is None:
                graphprinter_license_path.unlink(missing_ok=True)
            else:
                graphprinter_license_path.write_bytes(original_graphprinter_license)
        raise
    return plan

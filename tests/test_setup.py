from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ue_graph_capture.errors import CaptureError
from ue_graph_capture.setup_project import (
    GRAPHPRINTER_REVISION,
    _ensure_graphprinter_package,
    build_setup_plan,
    setup_project,
)


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "Project" / "MyGame.uproject"
    project.parent.mkdir()
    project.write_text(
        json.dumps(
            {
                "FileVersion": 3,
                "EngineAssociation": "5.7",
                "ProjectName": "MyGame",
                "Plugins": [{"Name": "Existing", "Enabled": True, "Extra": 1}],
            }
        ),
        encoding="utf-8",
    )
    return project


def _plugin_source(root: Path, name: str, marker: dict[str, str]) -> Path:
    source = root / (Path("unreal") / "UEGraphCapture" if name == "UEGraphCapture" else Path(name))
    source.mkdir(parents=True)
    (source / f"{name}.uplugin").write_text("{}", encoding="utf-8")
    (source / ".ue-graph-capture-managed.json").write_text(json.dumps(marker), encoding="utf-8")
    if name == "GraphPrinter":
        (source / "LICENSE").write_text("GraphPrinter MIT license\n", encoding="utf-8")
    return source


def test_setup_is_idempotent_and_preserves_existing_project_plugins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    sources = tmp_path / "sources"
    graphprinter = _plugin_source(sources, "GraphPrinter", {"revision": GRAPHPRINTER_REVISION})
    monkeypatch.setattr(
        "ue_graph_capture.setup_project._ensure_graphprinter_source",
        lambda allow_download: graphprinter,
    )
    monkeypatch.setattr(
        "ue_graph_capture.setup_project._ensure_graphprinter_package",
        lambda source, editor: graphprinter,
    )
    monkeypatch.setattr(
        "ue_graph_capture.setup_project.resolve_unreal_editor",
        lambda project, explicit: tmp_path / "UnrealEditor",
    )

    def fake_build(project_path: Path, editor_path: Path, plugin_path: Path) -> None:
        del project_path, editor_path
        binaries = plugin_path / "Binaries" / "Test"
        binaries.mkdir(parents=True)
        (binaries / "UEGraphCapture.dll").write_bytes(b"test")
        marker_path = plugin_path / ".ue-graph-capture-managed.json"
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker["built"] = True
        marker_path.write_text(json.dumps(marker), encoding="utf-8")

    monkeypatch.setattr("ue_graph_capture.setup_project._build_project_plugin", fake_build)

    first = setup_project(project)
    assert first.graphprinter_change is True
    assert (project.parent / "Plugins" / "GraphPrinter" / "GraphPrinter.uplugin").is_file()
    assert (project.parent / "Plugins" / "GraphPrinter" / "LICENSE").read_text(
        encoding="utf-8"
    ) == "GraphPrinter MIT license\n"
    assert (project.parent / "Plugins" / "UEGraphCapture" / "UEGraphCapture.uplugin").is_file()
    data = json.loads(project.read_text(encoding="utf-8"))
    assert data["Plugins"][0] == {"Name": "Existing", "Enabled": True, "Extra": 1}
    assert {item["Name"] for item in data["Plugins"]} == {
        "Existing",
        "GraphPrinter",
        "UEGraphCapture",
    }

    second = setup_project(project)
    assert second.graphprinter_change is False
    assert second.ue_plugin_change is False
    assert second.enablement_change is False


def test_setup_refuses_to_overwrite_unmanaged_plugin(tmp_path: Path) -> None:
    project = _project(tmp_path)
    destination = project.parent / "Plugins" / "GraphPrinter"
    destination.mkdir(parents=True)
    (destination / "GraphPrinter.uplugin").write_text("{}", encoding="utf-8")
    with pytest.raises(CaptureError, match="unmanaged"):
        build_setup_plan(project)


def test_graphprinter_package_copies_upstream_license(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "repo" / "Plugins" / "GraphPrinter"
    source.mkdir(parents=True)
    (source / "GraphPrinter.uplugin").write_text("{}", encoding="utf-8")
    upstream_license = source.parent.parent / "LICENSE"
    upstream_license.write_text("GraphPrinter MIT license\n", encoding="utf-8")
    editor = tmp_path / "UnrealEditor"
    editor.write_text("editor", encoding="utf-8")
    uat = tmp_path / "RunUAT.bat"
    uat.write_text("uat", encoding="utf-8")
    cache_root = tmp_path / "cache"

    class FakeAdapter:
        def run_uat_from_editor(self, editor_path: Path) -> Path:
            assert editor_path == editor
            return uat

    def fake_run_checked(command: list[str], *, cwd: Path | None = None):
        del cwd
        package_argument = next(item for item in command if item.startswith("-Package="))
        packaged = Path(package_argument.removeprefix("-Package=")) / "GraphPrinter"
        packaged.mkdir(parents=True)
        (packaged / "GraphPrinter.uplugin").write_text("{}", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(
        "ue_graph_capture.setup_project.graphprinter_cache_root", lambda: cache_root
    )
    monkeypatch.setattr("ue_graph_capture.setup_project.current_adapter", lambda: FakeAdapter())
    monkeypatch.setattr("ue_graph_capture.setup_project._run_checked", fake_run_checked)

    package = _ensure_graphprinter_package(source, editor)

    assert (package / "LICENSE").read_text(encoding="utf-8") == upstream_license.read_text(
        encoding="utf-8"
    )


def test_setup_repairs_license_for_existing_managed_graphprinter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    plugins = project.parent / "Plugins"
    graphprinter = plugins / "GraphPrinter"
    graphprinter.mkdir(parents=True)
    (graphprinter / "GraphPrinter.uplugin").write_text("{}", encoding="utf-8")
    (graphprinter / ".ue-graph-capture-managed.json").write_text(
        json.dumps({"revision": GRAPHPRINTER_REVISION}), encoding="utf-8"
    )
    ue_plugin = plugins / "UEGraphCapture"
    (ue_plugin / "Binaries" / "Win64").mkdir(parents=True)
    (ue_plugin / "Binaries" / "Win64" / "UEGraphCapture.dll").write_bytes(b"built")
    (ue_plugin / "UEGraphCapture.uplugin").write_text("{}", encoding="utf-8")
    (ue_plugin / ".ue-graph-capture-managed.json").write_text(
        json.dumps({"version": "0.1.0", "built": True}), encoding="utf-8"
    )
    package = tmp_path / "graphprinter-package"
    package.mkdir()
    (package / "GraphPrinter.uplugin").write_text("{}", encoding="utf-8")
    (package / "LICENSE").write_text("GraphPrinter MIT license\n", encoding="utf-8")
    monkeypatch.setattr(
        "ue_graph_capture.setup_project._ensure_graphprinter_source",
        lambda allow_download: graphprinter,
    )
    monkeypatch.setattr(
        "ue_graph_capture.setup_project._ensure_graphprinter_package",
        lambda source, editor: package,
    )
    monkeypatch.setattr(
        "ue_graph_capture.setup_project.resolve_unreal_editor",
        lambda project_path, explicit: tmp_path / "UnrealEditor",
    )

    plan = setup_project(project)

    assert plan.graphprinter_change is False
    assert plan.graphprinter_license_change is True
    assert (graphprinter / "LICENSE").read_text(encoding="utf-8") == "GraphPrinter MIT license\n"

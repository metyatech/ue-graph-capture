from __future__ import annotations

import json
from pathlib import Path

import pytest

from ue_graph_capture.errors import CaptureError
from ue_graph_capture.setup_project import (
    GRAPHPRINTER_REVISION,
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
    return source


def test_setup_is_idempotent_and_preserves_existing_project_plugins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    sources = tmp_path / "sources"
    graphprinter = _plugin_source(sources, "GraphPrinter", {"revision": GRAPHPRINTER_REVISION})
    _plugin_source(sources, "UEGraphCapture", {"version": "0.1.0"})
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
    monkeypatch.setattr("ue_graph_capture.setup_project._repo_root", lambda: sources)

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

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from ue_graph_capture import capture as capture_module
from ue_graph_capture.capture import capture_graph
from ue_graph_capture.editor import resolve_unreal_editor
from ue_graph_capture.errors import CaptureError
from ue_graph_capture.platforms.base import candidate_editor_from_install_dirs
from ue_graph_capture.process import run_editor_request
from ue_graph_capture.project import snapshot_project
from ue_graph_capture.validation import validate_output_path, validate_png

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "src" / "ue_graph_capture" / "bundled" / "UEGraphCapture"


def _write_project(path: Path, *, association: str = "5.7") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    project = path / "MyGame.uproject"
    project.write_text(
        '{"FileVersion": 3, "EngineAssociation": "' + association + '", "Plugins": []}\n',
        encoding="utf-8",
    )
    return project


def test_png_validation_rejects_single_color_and_accepts_graph_like_image(tmp_path: Path) -> None:
    solid = tmp_path / "solid.png"
    Image.new("RGB", (512, 512), "white").save(solid)
    with pytest.raises(CaptureError, match="single-color"):
        validate_png(solid)

    graph = tmp_path / "graph.png"
    image = Image.new("RGB", (512, 512), "white")
    draw = ImageDraw.Draw(image)
    for offset in range(20, 500, 20):
        draw.rectangle((offset, 20, min(offset + 45, 500), 80), outline="black", width=3)
        draw.line((offset, 100, min(offset + 90, 500), 480), fill="blue", width=4)
    image.save(graph)
    result = validate_png(graph)
    assert result["width"] == 512
    assert result["height"] == 512
    assert len(result["sha256"]) == 64


def test_candidate_editor_paths_are_platform_adapter_data(tmp_path: Path) -> None:
    candidates = candidate_editor_from_install_dirs(
        [tmp_path / "UE_5.7"], "Engine/Binaries/TestEditor"
    )
    assert candidates[0].path == (tmp_path / "UE_5.7" / "Engine/Binaries/TestEditor").resolve()
    assert candidates[0].source.endswith("UE_5.7")


def test_editor_resolution_prefers_explicit_path(tmp_path: Path) -> None:
    project = _write_project(tmp_path)
    explicit = tmp_path / "UnrealEditor"
    explicit.write_text("stub", encoding="utf-8")
    assert resolve_unreal_editor(project, explicit) == explicit.resolve()


class _TimeoutProcess:
    returncode = -15

    def __init__(self) -> None:
        self.terminated = False
        self.killed = False

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        if timeout is not None and not self.terminated and not self.killed:
            raise subprocess.TimeoutExpired("UnrealEditor", timeout)
        return "stdout", "stderr"

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


def test_timeout_terminates_editor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    process = _TimeoutProcess()
    monkeypatch.setattr(
        "ue_graph_capture.process.subprocess.Popen", lambda *args, **kwargs: process
    )
    with pytest.raises(CaptureError, match="timed out"):
        run_editor_request(
            tmp_path / "UnrealEditor", tmp_path / "MyGame.uproject", tmp_path / "request.json", 1
        )
    assert process.terminated is True


def test_capture_snapshot_covers_uproject_config_and_target_asset(tmp_path: Path) -> None:
    project = _write_project(tmp_path)
    (tmp_path / "Config").mkdir()
    (tmp_path / "Config" / "DefaultEditor.ini").write_text(
        "[/Script/Engine.Engine]\n", encoding="utf-8"
    )
    (tmp_path / "Content" / "Blueprints").mkdir(parents=True)
    asset_path = tmp_path / "Content" / "Blueprints" / "BP_Player.uasset"
    asset_path.write_bytes(b"asset")
    snapshot = snapshot_project(project, "/Game/Blueprints/BP_Player")
    relative_paths = {item.relative_path for item in snapshot}
    assert "MyGame.uproject" in relative_paths
    assert "Config/DefaultEditor.ini" in relative_paths
    assert "Content/Blueprints/BP_Player.uasset" in relative_paths


@pytest.mark.parametrize(
    "relative_output",
    [
        "MyGame.uproject",
        "Content/BP.png",
        "Config/test.png",
        "Plugins/test.png",
    ],
)
def test_capture_output_rejects_project_descriptor_and_protected_trees(
    tmp_path: Path, relative_output: str
) -> None:
    project = _write_project(tmp_path)
    with pytest.raises(CaptureError, match="must not overwrite|Content, Config, and Plugins"):
        validate_output_path(project, tmp_path / relative_output)


def test_capture_output_requires_png_case_insensitively_and_allows_external_png(
    tmp_path: Path,
) -> None:
    project = _write_project(tmp_path / "Project")
    with pytest.raises(CaptureError, match=r"\.png extension"):
        validate_output_path(project, tmp_path / "captures" / "BP.jpg")
    assert (
        validate_output_path(project, tmp_path / "captures" / "BP.PNG")
        == (tmp_path / "captures" / "BP.PNG").resolve()
    )


def test_capture_output_safety_fails_before_plugin_or_editor_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _write_project(tmp_path)

    def plugin_check_must_not_run(project_path: Path) -> None:
        del project_path
        raise AssertionError("plugin check ran before output safety validation")

    monkeypatch.setattr(
        "ue_graph_capture.capture._assert_plugins_installed", plugin_check_must_not_run
    )
    with pytest.raises(CaptureError, match="Content, Config, and Plugins"):
        capture_graph(
            project=project,
            asset="/Game/BP_Player",
            graph="EventGraph",
            output=tmp_path / "Content" / "blocked.png",
            editor=None,
            scale=1.0,
            padding=100,
            timeout=10,
        )


def test_capture_detects_project_mutation_after_png_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _write_project(tmp_path)
    config = tmp_path / "Config" / "DefaultEditor.ini"
    config.parent.mkdir()
    config.write_text("[Editor]\n", encoding="utf-8")
    output = tmp_path / "captures" / "BP_Player.PNG"
    events: list[str] = []

    def fake_run_request(*, request, **kwargs):
        del kwargs
        image = Image.new("RGB", (512, 512), "white")
        draw = ImageDraw.Draw(image)
        for offset in range(20, 500, 20):
            draw.rectangle((offset, 20, min(offset + 45, 500), 80), outline="black", width=3)
            draw.line((offset, 100, min(offset + 90, 500), 480), fill="blue", width=4)
        request.output_directory.mkdir(parents=True, exist_ok=True)
        staged = request.output_directory / "capture.png"
        image.save(staged)
        config.write_text("[Editor]\nMutated=true\n", encoding="utf-8")
        return {"ok": True, "output": str(staged)}

    original_snapshot = snapshot_project
    original_publish = capture_module._publish_png

    def tracked_snapshot(project_path: Path, asset: str):
        events.append("snapshot")
        return original_snapshot(project_path, asset)

    def tracked_publish(staged: Path, destination: Path) -> Path:
        events.append("publish")
        return original_publish(staged, destination)

    monkeypatch.setattr("ue_graph_capture.capture._assert_plugins_installed", lambda project: None)
    monkeypatch.setattr("ue_graph_capture.capture._run_request", fake_run_request)
    monkeypatch.setattr("ue_graph_capture.capture.snapshot_project", tracked_snapshot)
    monkeypatch.setattr("ue_graph_capture.capture._publish_png", tracked_publish)

    with pytest.raises(CaptureError, match="source-facing project file"):
        capture_graph(
            project=project,
            asset="/Game/BP_Player",
            graph="EventGraph",
            output=output,
            editor=None,
            scale=1.0,
            padding=100,
            timeout=10,
        )
    assert events == ["snapshot", "publish", "snapshot"]
    assert output.is_file()


def test_core_runtime_has_no_forbidden_platform_automation_or_shell_calls() -> None:
    python_source = "\n".join(
        path.read_text(encoding="utf-8") for path in (REPO_ROOT / "src").rglob("*.py")
    )
    lowered = python_source.lower()
    assert "powershell" not in lowered
    assert "printwindow" not in lowered
    assert "sendinput" not in lowered
    assert "shell=true" not in lowered.replace(" ", "")
    for path in PLUGIN_ROOT.rglob("*.cpp"):
        source = path.read_text(encoding="utf-8").lower()
        assert "sendinput" not in source
        assert "printwindow" not in source


def test_editor_plugin_waits_for_graph_layout_and_png_completion() -> None:
    source = (
        PLUGIN_ROOT / "Source" / "UEGraphCapture" / "Private" / "UEGraphCaptureModule.cpp"
    ).read_text(encoding="utf-8")
    assert "bPrintPending" in source
    assert "GetBoundsForSelectedNodes" in source
    assert "Graph widgets are laid out; starting GraphPrinter." in source
    waiting_start = source.index("if (bWaitingForPng)")
    assert "return true;" in source[waiting_start:]


def test_process_source_uses_list_arguments_and_shell_false() -> None:
    source = (REPO_ROOT / "src" / "ue_graph_capture" / "process.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    popen_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "Popen"
    ]
    assert popen_calls
    assert any(
        keyword.arg == "shell" and keyword.value.value is False
        for keyword in popen_calls[0].keywords
    )

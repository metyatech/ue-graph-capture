from __future__ import annotations

import json
from pathlib import Path

import pytest

from ue_graph_capture.cli import build_parser
from ue_graph_capture.errors import CaptureError
from ue_graph_capture.models import CaptureRequest
from ue_graph_capture.project import update_plugin_enablement, write_json_atomic
from ue_graph_capture.request_io import read_result, write_request
from ue_graph_capture.validation import (
    safe_output_filename,
    validate_asset_path,
    validate_graph_name,
)


def test_help_and_version_are_discoverable(capsys: pytest.CaptureFixture[str]) -> None:
    parser = build_parser()
    with pytest.raises(SystemExit) as help_exit:
        parser.parse_args(["--help"])
    assert help_exit.value.code == 0
    help_text = capsys.readouterr().out
    assert "ue-graph-capture capture -h" in help_text
    with pytest.raises(SystemExit) as version_exit:
        parser.parse_args(["--version"])
    assert version_exit.value.code == 0
    assert "ue-graph-capture 0.1.0" in capsys.readouterr().out


@pytest.mark.parametrize("asset", ["BP_Player", "Game/BP_Player", "/Content/BP_Player", "/Game/"])
def test_asset_path_validation_rejects_non_package_paths(asset: str) -> None:
    with pytest.raises(CaptureError):
        validate_asset_path(asset)


def test_asset_and_graph_validation_accept_expected_values() -> None:
    assert validate_asset_path("/Game/Blueprints/BP_Player") == "/Game/Blueprints/BP_Player"
    assert validate_graph_name("EventGraph") == "EventGraph"
    with pytest.raises(CaptureError):
        validate_graph_name("/Game/EventGraph")


def test_default_output_filename_is_safe() -> None:
    assert (
        safe_output_filename("/Game/Blueprints/BP Player", "Event Graph")
        == "BP_Player_Event_Graph.png"
    )


def test_request_json_serialization_and_result_parsing(tmp_path: Path) -> None:
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    request = CaptureRequest(
        action="capture",
        project=tmp_path / "MyGame.uproject",
        asset="/Game/BP_Player",
        graph="EventGraph",
        output_directory=tmp_path / "output",
        result_path=result_path,
        scale=1.25,
        padding=80,
    )
    write_request(request_path, request)
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["action"] == "capture"
    assert payload["scale"] == 1.25
    result_path.write_text('{"ok": true, "graphs": []}', encoding="utf-8")
    assert read_result(result_path)["ok"] is True


def test_plugin_enablement_preserves_existing_fields() -> None:
    original = {
        "FileVersion": 3,
        "ProjectName": "MyGame",
        "Plugins": [
            {"Name": "ExistingPlugin", "Enabled": False, "OptionalField": "preserve"},
            {"Name": "GraphPrinter", "Enabled": False, "LoadingPhase": "Default"},
        ],
    }
    updated, changed = update_plugin_enablement(original)
    assert changed is True
    assert updated["Plugins"][0] == original["Plugins"][0]
    assert updated["Plugins"][1]["Enabled"] is True
    assert updated["Plugins"][1]["LoadingPhase"] == "Default"
    assert updated["Plugins"][-1] == {"Name": "UEGraphCapture", "Enabled": True}


def test_write_json_atomic_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "project.json"
    write_json_atomic(target, {"value": "保持"})
    assert json.loads(target.read_text(encoding="utf-8")) == {"value": "保持"}

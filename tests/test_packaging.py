from __future__ import annotations

from importlib import resources


def _bundled_plugin():
    return resources.files("ue_graph_capture").joinpath("bundled", "UEGraphCapture")


def test_bundled_ue_graph_capture_resource_contains_source_files() -> None:
    plugin = _bundled_plugin()
    assert plugin.joinpath("UEGraphCapture.uplugin").is_file()
    assert plugin.joinpath("UEGraphCapture.managed.json").is_file()
    assert plugin.joinpath(
        "Source", "UEGraphCapture", "Private", "UEGraphCaptureModule.cpp"
    ).is_file()
    assert plugin.joinpath("Source", "UEGraphCapture", "UEGraphCapture.Build.cs").is_file()

    def walk(resource):
        for child in resource.iterdir():
            if child.is_dir():
                yield from walk(child)
            else:
                yield child

    names = {child.name for child in walk(plugin)}
    assert "Binaries" not in names
    assert "Intermediate" not in names

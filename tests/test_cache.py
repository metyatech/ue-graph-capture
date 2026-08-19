from __future__ import annotations

import importlib
import subprocess
from pathlib import Path

import pytest

from ue_graph_capture.setup_project import _ensure_graphprinter_source

setup_project_module = importlib.import_module("ue_graph_capture.setup_project")


def _git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        shell=False,
    )
    return result.stdout.strip()


def _repository(tmp_path: Path, name: str, *, plugin: bool) -> tuple[Path, str]:
    repository = tmp_path / name
    _git("init", str(repository))
    _git("config", "user.email", "test@example.invalid", cwd=repository)
    _git("config", "user.name", "Test User", cwd=repository)
    if plugin:
        plugin_path = repository / "Plugins" / "GraphPrinter"
        plugin_path.mkdir(parents=True)
        (plugin_path / "GraphPrinter.uplugin").write_text("{}\n", encoding="utf-8")
        (repository / "LICENSE").write_text("GraphPrinter MIT license\n", encoding="utf-8")
    (repository / "tracked.txt").write_text("clean\n", encoding="utf-8")
    _git("add", ".", cwd=repository)
    _git("commit", "-m", "initial", cwd=repository)
    return repository, _git("rev-parse", "HEAD", cwd=repository)


def _bare_remote(tmp_path: Path, source: Path) -> Path:
    remote = tmp_path / "remote.git"
    _git("clone", "--bare", str(source), str(remote))
    return remote


def test_graphprinter_cache_resets_dirty_state_and_pins_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, revision = _repository(tmp_path, "source", plugin=True)
    remote = _bare_remote(tmp_path, source)
    cache_root = tmp_path / "cache"
    monkeypatch.setattr(setup_project_module, "GRAPHPRINTER_URL", str(remote))
    monkeypatch.setattr(setup_project_module, "GRAPHPRINTER_REVISION", revision)
    monkeypatch.setattr(setup_project_module, "graphprinter_cache_root", lambda: cache_root)

    plugin = _ensure_graphprinter_source(allow_download=True)
    repository = cache_root / "source"
    (repository / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    (repository / "untracked.txt").write_text("remove me\n", encoding="utf-8")

    assert _ensure_graphprinter_source(allow_download=True) == plugin
    assert _git("rev-parse", "HEAD", cwd=repository) == revision
    assert _git("status", "--porcelain", cwd=repository) == ""
    assert (repository / "tracked.txt").read_text(encoding="utf-8") == "clean\n"
    assert not (repository / "untracked.txt").exists()
    assert (repository / "Plugins" / "GraphPrinter" / "GraphPrinter.uplugin").is_file()
    assert (repository / "LICENSE").is_file()


def test_graphprinter_cache_recreates_wrong_origin_without_resetting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected_source, revision = _repository(tmp_path, "expected", plugin=True)
    expected_remote = _bare_remote(tmp_path, expected_source)
    wrong_source, _ = _repository(tmp_path, "wrong", plugin=False)
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    _git("clone", str(wrong_source), str(cache_root / "source"))

    monkeypatch.setattr(setup_project_module, "GRAPHPRINTER_URL", str(expected_remote))
    monkeypatch.setattr(setup_project_module, "GRAPHPRINTER_REVISION", revision)
    monkeypatch.setattr(setup_project_module, "graphprinter_cache_root", lambda: cache_root)

    plugin = _ensure_graphprinter_source(allow_download=True)

    assert plugin == cache_root / "source" / "Plugins" / "GraphPrinter"
    assert _git("rev-parse", "HEAD", cwd=cache_root / "source") == revision
    assert _git("remote", "get-url", "origin", cwd=cache_root / "source") == str(expected_remote)
    assert (plugin / "GraphPrinter.uplugin").is_file()
    assert wrong_source.exists()

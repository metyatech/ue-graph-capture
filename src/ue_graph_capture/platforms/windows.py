from __future__ import annotations

import json
import os
from pathlib import Path

from .base import EditorCandidate, candidate_editor_from_install_dirs


def editor_from_install_dir(install_dir: Path) -> Path:
    return install_dir / "Engine" / "Binaries" / "Win64" / "UnrealEditor.exe"


def run_uat_from_editor(editor: Path) -> Path:
    return editor.parents[3] / "Engine" / "Build" / "BatchFiles" / "RunUAT.bat"


def ubt_command_from_editor(editor: Path) -> list[Path]:
    engine_dir = editor.parents[2]
    dotnet_candidates = sorted(
        (engine_dir / "Binaries" / "ThirdParty" / "DotNet").glob("*/win-x64/dotnet.exe"),
        reverse=True,
    )
    dotnet = dotnet_candidates[0] if dotnet_candidates else None
    ubt = engine_dir / "Binaries" / "DotNET" / "UnrealBuildTool" / "UnrealBuildTool.dll"
    if dotnet is None or not ubt.is_file():
        missing = dotnet if dotnet is None else ubt
        raise FileNotFoundError(f"UnrealBuildTool runtime was not found: {missing}")
    return [dotnet, ubt]


def ubt_platform_name() -> str:
    return "Win64"


def _registry_install_dirs(association: str) -> list[Path]:
    try:
        import winreg
    except ImportError:
        return []

    result: list[Path] = []
    subkey = r"SOFTWARE\EpicGames\Unreal Engine\Builds"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, subkey) as key:
                for index in range(winreg.QueryInfoKey(key)[1]):
                    name, value, _ = winreg.EnumValue(key, index)
                    if name == association or name == f"UE_{association}":
                        result.append(Path(value))
        except OSError:
            continue
    return result


def _launcher_install_dirs(association: str) -> list[Path]:
    program_data = os.environ.get("PROGRAMDATA")
    if not program_data:
        return []
    manifest = (
        Path(program_data)
        / "Epic"
        / "EpicGamesLauncher"
        / "Data"
        / "Manifests"
        / "LauncherInstalled.dat"
    )
    if not manifest.is_file():
        return []
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    result: list[Path] = []
    for item in data.get("InstallationList", []):
        if not isinstance(item, dict):
            continue
        app_name = str(item.get("AppName", ""))
        if association in app_name or app_name == f"UE_{association}":
            install_location = item.get("InstallLocation")
            if isinstance(install_location, str):
                result.append(Path(install_location))
    return result


def editor_candidates(association: str) -> list[EditorCandidate]:
    version = association.replace(".", "_")
    install_dirs = [
        Path(value)
        for key, value in os.environ.items()
        if key.upper() in {f"UE_{version}_ROOT", "UNREAL_ENGINE_ROOT"}
    ]
    install_dirs.extend(_registry_install_dirs(association))
    install_dirs.extend(_launcher_install_dirs(association))
    install_dirs.extend(
        Path(base) / f"UE_{association}"
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files") + r"\Epic Games",)
    )
    return candidate_editor_from_install_dirs(
        install_dirs, r"Engine\Binaries\Win64\UnrealEditor.exe"
    )

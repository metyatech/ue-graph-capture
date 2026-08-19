from __future__ import annotations

import os
from pathlib import Path

from .base import EditorCandidate, candidate_editor_from_install_dirs


def editor_from_install_dir(install_dir: Path) -> Path:
    return install_dir / "Engine" / "Binaries" / "Linux" / "UnrealEditor"


def run_uat_from_editor(editor: Path) -> Path:
    return editor.parents[3] / "Engine" / "Build" / "BatchFiles" / "RunUAT.sh"


def ubt_command_from_editor(editor: Path) -> list[Path]:
    engine_dir = editor.parents[2]
    dotnet_candidates = sorted(
        (engine_dir / "Binaries" / "ThirdParty" / "DotNet").glob("*/linux-x64/dotnet"),
        reverse=True,
    )
    dotnet = dotnet_candidates[0] if dotnet_candidates else None
    ubt = engine_dir / "Binaries" / "DotNET" / "UnrealBuildTool" / "UnrealBuildTool.dll"
    if dotnet is None or not ubt.is_file():
        missing = dotnet if dotnet is None else ubt
        raise FileNotFoundError(f"UnrealBuildTool runtime was not found: {missing}")
    return [dotnet, ubt]


def ubt_platform_name() -> str:
    return "Linux"


def editor_candidates(association: str) -> list[EditorCandidate]:
    install_dirs = [
        Path(value)
        for key, value in os.environ.items()
        if key.upper() in {f"UE_{association.replace('.', '_')}_ROOT", "UNREAL_ENGINE_ROOT"}
    ]
    install_dirs.extend(
        [
            Path.home() / "UnrealEngine",
            Path.home() / f"UnrealEngine_{association}",
            Path("/opt/UnrealEngine") / f"UE_{association}",
        ]
    )
    return candidate_editor_from_install_dirs(install_dirs, "Engine/Binaries/Linux/UnrealEditor")

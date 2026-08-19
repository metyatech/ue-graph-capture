from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EditorCandidate:
    path: Path
    source: str


def candidate_editor_from_install_dirs(
    install_dirs: list[Path], executable_relative_path: str
) -> list[EditorCandidate]:
    candidates: list[EditorCandidate] = []
    seen: set[Path] = set()
    for install_dir in install_dirs:
        candidate = (install_dir / executable_relative_path).resolve(strict=False)
        if candidate not in seen:
            candidates.append(EditorCandidate(candidate, str(install_dir)))
            seen.add(candidate)
    return candidates

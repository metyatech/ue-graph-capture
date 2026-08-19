from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import CaptureError


@dataclass(frozen=True)
class EditorProcessResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def run_editor_request(
    editor: Path, project: Path, request_path: Path, timeout: float
) -> EditorProcessResult:
    command = [
        str(editor),
        str(project),
        "-Unattended",
        "-NoSplash",
        "-NoSound",
        "-NoSourceControl",
        "-NoSave",
        "-stdout",
        f"-UEGraphCaptureRequest={request_path}",
    ]
    try:
        process = subprocess.Popen(
            command,
            cwd=project.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
    except OSError as exc:
        raise CaptureError(f"Unable to start Unreal Editor {editor}: {exc}") from exc

    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
        raise CaptureError(
            f"Unreal Editor timed out after {timeout:g} seconds.\n"
            f"{_diagnostic_tail(stdout, stderr)}"
        ) from exc

    return EditorProcessResult(process.returncode, stdout or "", stderr or "")


def _diagnostic_tail(stdout: str, stderr: str) -> str:
    text = "\n".join(part for part in (stdout, stderr) if part).strip()
    return text[-4000:] if text else "No Unreal Editor output was captured."

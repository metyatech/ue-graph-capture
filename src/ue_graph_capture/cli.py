from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from typing import Any

from . import __version__
from .capture import capture_graph, list_graphs
from .doctor import run_doctor
from .errors import CaptureError
from .project import resolve_project_path
from .setup_project import setup_project


def _positive_float(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def _nonnegative_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return value


def _common_output_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show diagnostic progress.")
    parser.add_argument("--quiet", action="store_true", help="Suppress human-readable progress.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ue-graph-capture",
        description=(
            "Capture the complete visible and off-screen area of an Unreal Blueprint graph."
        ),
        epilog=(
            "Primary workflow: setup -> capture. Use `ue-graph-capture capture -h` for the "
            "complete capture command, or `ue-graph-capture <command> -h` for a subcommand."
        ),
    )
    parser.add_argument("--version", "-V", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser(
        "setup",
        help="Install the pinned UEGraphCapture and GraphPrinter plugins into a project.",
        description=(
            "Prepare one project for one-shot graph capture. This is the only mutating command."
        ),
        epilog="Example: ue-graph-capture setup --project /path/to/MyGame.uproject",
    )
    setup_parser.add_argument("--project", required=True, help="Path to a .uproject file.")
    setup_parser.add_argument(
        "--unreal-editor",
        help="Explicit UnrealEditor executable path for GraphPrinter packaging.",
    )
    setup_parser.add_argument(
        "--dry-run", action="store_true", help="Show changes without writing files."
    )
    setup_parser.add_argument(
        "--yes", action="store_true", help="Explicitly confirm project setup."
    )
    _common_output_options(setup_parser)
    setup_parser.set_defaults(handler=_handle_setup)

    capture_parser = subparsers.add_parser(
        "capture",
        help="Open one Blueprint graph and write a complete PNG with GraphPrinter.",
        description="Capture one EventGraph, Function Graph, or Macro Graph without UI automation.",
        epilog=(
            "Example: ue-graph-capture capture --project /path/to/MyGame.uproject "
            "--asset /Game/Blueprints/BP_Player --graph EventGraph --output ./BP_Player.png"
        ),
    )
    capture_parser.add_argument("--project", required=True, help="Path to a .uproject file.")
    capture_parser.add_argument(
        "--asset", required=True, help="Blueprint package path, e.g. /Game/Blueprints/BP_Player."
    )
    capture_parser.add_argument("--graph", required=True, help="Exact graph name, e.g. EventGraph.")
    capture_parser.add_argument(
        "--output", help="PNG destination; defaults to <asset>_<graph>.png."
    )
    capture_parser.add_argument("--unreal-editor", help="Explicit UnrealEditor executable path.")
    capture_parser.add_argument(
        "--scale", type=_positive_float, default=1.0, help="GraphPrinter rendering scale."
    )
    capture_parser.add_argument(
        "--padding",
        type=_nonnegative_int,
        default=100,
        help="GraphPrinter graph padding in pixels.",
    )
    capture_parser.add_argument(
        "--timeout", type=_positive_float, default=300.0, help="Editor timeout in seconds."
    )
    _common_output_options(capture_parser)
    capture_parser.set_defaults(handler=_handle_capture)

    list_parser = subparsers.add_parser(
        "list-graphs",
        help="List supported graph names and types from one Blueprint.",
        description="Load one Blueprint in Unreal Editor and list its graph names and types.",
        epilog=(
            "Example: ue-graph-capture list-graphs --project MyGame.uproject "
            "--asset /Game/Blueprints/BP_Player"
        ),
    )
    list_parser.add_argument("--project", required=True, help="Path to a .uproject file.")
    list_parser.add_argument("--asset", required=True, help="Blueprint package path.")
    list_parser.add_argument("--unreal-editor", help="Explicit UnrealEditor executable path.")
    list_parser.add_argument(
        "--timeout", type=_positive_float, default=120.0, help="Editor timeout in seconds."
    )
    _common_output_options(list_parser)
    list_parser.set_defaults(handler=_handle_list_graphs)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Check Python, project, Unreal Editor, and plugin installation.",
        description="Run read-only environment checks for a project.",
        epilog="Example: ue-graph-capture doctor --project /path/to/MyGame.uproject --json",
    )
    doctor_parser.add_argument("--project", required=True, help="Path to a .uproject file.")
    doctor_parser.add_argument("--unreal-editor", help="Explicit UnrealEditor executable path.")
    _common_output_options(doctor_parser)
    doctor_parser.set_defaults(handler=_handle_doctor)
    return parser


def _emit(data: Any, *, as_json: bool, quiet: bool = False) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
    elif not quiet:
        if isinstance(data, dict):
            for key, value in data.items():
                print(f"{key}: {value}")
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    print(f"{item.get('name', '<unnamed>')} ({item.get('type', 'unknown')})")
                else:
                    print(item)
        else:
            print(data)


def _handle_setup(args: argparse.Namespace) -> int:
    project = resolve_project_path(args.project)
    plan = setup_project(project, dry_run=args.dry_run, editor=args.unreal_editor)
    _emit(plan.to_dict(), as_json=args.json, quiet=args.quiet)
    if args.dry_run and not args.quiet and not args.json:
        print("Dry run: no project files were changed.")
    return 0


def _handle_capture(args: argparse.Namespace) -> int:
    project = resolve_project_path(args.project)
    result = capture_graph(
        project=project,
        asset=args.asset,
        graph=args.graph,
        output=args.output,
        editor=args.unreal_editor,
        scale=args.scale,
        padding=args.padding,
        timeout=args.timeout,
    )
    _emit(result, as_json=args.json, quiet=args.quiet)
    return 0


def _handle_list_graphs(args: argparse.Namespace) -> int:
    project = resolve_project_path(args.project)
    graphs = list_graphs(
        project=project,
        asset=args.asset,
        editor=args.unreal_editor,
        timeout=args.timeout,
    )
    _emit(graphs, as_json=args.json, quiet=args.quiet)
    return 0


def _handle_doctor(args: argparse.Namespace) -> int:
    result = run_doctor(args.project, args.unreal_editor)
    _emit(result, as_json=args.json, quiet=args.quiet)
    return 0 if result.get("ok") is True else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        handler: Callable[[argparse.Namespace], int] = args.handler
        return handler(args)
    except CaptureError as exc:
        as_json = bool("args" in locals() and getattr(args, "json", False))
        if as_json:
            print(
                json.dumps({"ok": False, "error": exc.message, **exc.details}, ensure_ascii=False)
            )
        else:
            print(f"error: {exc.message}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return 130

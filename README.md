# ue-graph-capture

`ue-graph-capture` is a Python 3.10+ CLI that opens one Unreal Engine Blueprint graph and writes a PNG of the graph's complete off-screen area. The Unreal Editor is launched for one request, the editor-only `UEGraphCapture` plugin consumes that request, GraphPrinter renders the graph, and the editor exits.

## What it does

```text
ue-graph-capture setup   -> install the two editor plugins in one .uproject
ue-graph-capture doctor  -> read-only environment checks
ue-graph-capture list-graphs -> list exact graph names and types
ue-graph-capture capture -> one-shot GraphPrinter PNG + result metadata
```

The core runtime is Python and uses `argparse`, `pathlib`, and list-form subprocess arguments. It does not use PowerShell, `cmd.exe`, `shell=True`, Win32 screenshot APIs, AppleScript, X11 automation, mouse coordinates, HTTP servers, or WebSocket servers.

## Why GraphPrinter

GraphPrinter is the renderer for the Blueprint graph. The plugin is not forked or vendored into this repository. `setup` downloads and caches the upstream source at:

- Repository: <https://github.com/Naotsun19B/GraphPrinter>
- Pinned revision: `9c42bba098926c2066cf52877909d9b3ccd26d9f`
- Upstream plugin version: `3.2`

The integration uses GraphPrinter's public `UGenericGraphPrinter`, `UPrintGraphOptions`, and `UWidgetPrinter` APIs. It explicitly supplies the selected `SGraphEditor`, `All` scope, PNG export, rendering scale, padding, and Draw Only Graph. No GraphPrinter source patch is required.

GraphPrinter is MIT-licensed by Naotsun. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and the upstream [LICENSE](https://github.com/Naotsun19B/GraphPrinter/blob/9c42bba098926c2066cf52877909d9b3ccd26d9f/LICENSE).

## Requirements and support

- Python 3.10 or newer
- Unreal Engine 5.7 (the plugin targets the UE 5.7 editor APIs)
- Windows, macOS, or Linux; platform verification status is listed below
- Git for the first `setup` of a project, to download GraphPrinter into the user cache
- Pillow for PNG validation

The Unreal Editor is resolved from an explicit `--unreal-editor` path first. Otherwise the CLI reads `.uproject` `EngineAssociation` and asks the platform adapter for best-effort candidates. If resolution fails, pass the editor executable explicitly.

### Platform verification status

| Platform | Status | Evidence |
| --- | --- | --- |
| Windows | Verified with Unreal Engine 5.7.4 | Local end-to-end smoke completed: setup, doctor, list-graphs, and EventGraph PNG capture |
| macOS | Supported design; UE integration verification pending | Platform adapter and Python unit tests are covered by CI |
| Linux | Supported design; UE integration verification pending | Platform adapter and Python unit tests are covered by CI |

GraphPrinter upstream platform support is separate from this project's integration verification status. GitHub Actions runs Python-only checks and does not install Unreal Engine.

## Install locally

From this repository:

```bash
python -m pip install -e ".[dev]"
ue-graph-capture --help
ue-graph-capture --version
```

The canonical test command is:

```bash
python -m pytest -q
```

The CI quality gates also run:

```bash
python -m ruff format --check .
python -m ruff check .
```

## Setup

`setup` is the only command that changes a project. It is idempotent for installations managed by this tool and refuses to overwrite an unmanaged existing plugin directory.

```bash
ue-graph-capture setup --project /path/to/MyGame.uproject
```

Use `--dry-run` to inspect the planned changes without writing anything:

```bash
ue-graph-capture setup --project /path/to/MyGame.uproject --dry-run --json
```

Setup changes only the selected project's `Plugins/` directory and `.uproject` plugin enablement:

1. Copies the editor-only `UEGraphCapture` plugin from this repository.
2. Downloads GraphPrinter at the pinned revision into the user cache, builds its plugin with Unreal's `RunUAT BuildPlugin`, and copies the packaged plugin directory.
3. Adds or enables `UEGraphCapture` and `GraphPrinter` in the `.uproject` `Plugins` array while preserving other plugin entries and fields.
4. Compiles `UEGraphCapture` for the project's Unreal Editor target with UnrealBuildTool so the editor can load the plugin.

Setup may create generated `Binaries/` and `Intermediate/` files below the two project plugins as part of the build. No Content asset, Config file, or source repository file is changed except for the intended `.uproject` enablement.

## Capture

```bash
ue-graph-capture capture \
  --project /path/to/MyGame.uproject \
  --asset /Game/Blueprints/BP_Player \
  --graph EventGraph \
  --output ./BP_Player.png
```

The exact graph name is required. v0.1 supports EventGraph, Function Graph, and Macro Graph names returned by Unreal's Blueprint graph APIs. `--output` is optional; the default is `<AssetName>_<GraphName>.png` in the current directory.

Useful controls:

```bash
ue-graph-capture capture --project MyGame.uproject --asset /Game/Blueprints/BP_Player --graph EventGraph --scale 1.0 --padding 100 --timeout 300 --json
```

After GraphPrinter writes the staged PNG, Python validates that it exists, is a valid PNG, has positive dimensions, is not blank/single-color, and has a reasonable file size. Only then is it atomically published to the requested output path. The JSON result contains the project, asset, graph, graph type, output, dimensions, file size, SHA-256, UE version, GraphPrinter version/revision, and duration.

Capture does not compile or save the Blueprint. It snapshots `.uproject`, `Config`, and the requested Content asset before and after the one-shot editor run and fails if any of those source-facing files change.

## List graphs

```bash
ue-graph-capture list-graphs \
  --project /path/to/MyGame.uproject \
  --asset /Game/Blueprints/BP_Player

ue-graph-capture list-graphs \
  --project /path/to/MyGame.uproject \
  --asset /Game/Blueprints/BP_Player \
  --json
```

If a requested graph does not exist, the plugin reports the asset path, requested name, and all available graph names.

## Doctor

```bash
ue-graph-capture doctor --project /path/to/MyGame.uproject
ue-graph-capture doctor --project /path/to/MyGame.uproject --unreal-editor /path/to/UnrealEditor --json
```

Doctor checks the Python version, project existence, Unreal Editor resolution and version, GraphPrinter installation, and UEGraphCapture installation. It is read-only.

## One-shot protocol

The CLI writes a temporary absolute `request.json`, launches the editor with `-UEGraphCaptureRequest=<path>`, and waits for a temporary `result.json`. The plugin loads the Blueprint through `UBlueprint`, finds the exact `UEdGraph`, opens it through `IBlueprintEditor::OpenGraphAndBringToFront`, waits for the graph widget layout to become measurable, and passes the resulting graph widget to GraphPrinter. The plugin writes the result and requests a clean editor exit. No resident bridge is started.

GraphPrinter's public print call is asynchronous and does not expose a completion delegate on the public wrapper. The plugin therefore polls its private staging directory from the Unreal ticker with a fixed deadline and requires exactly one new PNG. This is the only bounded polling boundary; it is documented here because the upstream API does not expose a stronger completion event.

## Limitations

- v0.1 captures one graph per invocation; `capture-all` is intentionally not implemented.
- Unreal Engine 5.7 is the supported UE version. The C++ code keeps graph selection and renderer integration isolated so later UE versions can be adapted without changing the Python request protocol.
- GraphPrinter must be available as a project plugin after `setup`; capture does not silently mutate the project or install dependencies.
- This tool is for graph editor views. Timeline, Designer, Widget Blueprint view, and arbitrary editor-window screenshots are outside v0.1.
- The first setup needs network access to clone the pinned GraphPrinter source. The cache is reused on later projects.

## Development

```bash
python -m pip install -e ".[dev]"
python -m ruff format .
python -m ruff check .
python -m pytest -q
```

The GitHub Actions unit matrix covers Windows, macOS, and Linux with Python 3.10, 3.12, and 3.14. Unreal Engine is not installed in CI; the Windows real-editor smoke is a local verification step for this workstation. macOS and Linux UE integration verification remains pending.

## License

This repository is released under the MIT License. See [LICENSE](LICENSE). Third-party notices and attribution are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

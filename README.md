# GCode AI IDE

<p align="center">
  <img alt="GCode AI IDE" src="https://img.shields.io/badge/GCode-AI%20IDE-7c3aed?style=for-the-badge">
  <img alt="Local first" src="https://img.shields.io/badge/Local--First-0ea5e9?style=for-the-badge">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11+-3776ab?style=for-the-badge&logo=python&logoColor=white">
  <img alt="Flask" src="https://img.shields.io/badge/Backend-Flask-111827?style=for-the-badge&logo=flask">
  <img alt="Vanilla JS" src="https://img.shields.io/badge/Frontend-Vanilla%20JS-facc15?style=for-the-badge&logo=javascript&logoColor=111827">
</p>

**GCode is a local AI coding workspace that turns chat into reviewed file changes.**

Instead of dumping long code blocks into a conversation, GCode plans the work, prepares real files, stages edits, lets you review or reject them, runs safe checks when allowed, and keeps a local memory layer for long coding sessions.

It is built for developers who want an AI assistant that behaves more like a cautious local coding environment than a remote chatbot.

## Quick Start

From the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
install-gcode-command.cmd
```

When the setup wizard opens, choose the default interface for the `gcode` command.

Then open a new Command Prompt and run:

```cmd
gcode
```

You can change the default interface later:

```cmd
gcode setup
```

Run the browser UI directly without installing the launcher:

```powershell
python app.py
```

Then open:

```text
http://localhost:5000
```

## What GCode Does

GCode combines a chat interface, a file edit review system, a command safety layer, and a local project memory system.

Typical flow:

1. Ask GCode to build, fix, or refactor something.
2. GCode creates a plan and decides which files need to change.
3. Generated changes are staged as an edit batch.
4. You review the files and diffs.
5. You accept, reject, undo, or redo the batch.
6. GCode can run safe validation commands when the selected security mode allows it.
7. The final response summarizes what changed instead of flooding the chat with full files.

## Highlights

| Feature | What it is for |
| --- | --- |
| Browser IDE | Full local web interface served by Flask |
| Terminal UI | Keyboard-first interface for fast local sessions |
| Desktop wrapper | Optional `pywebview` window around the local web UI |
| Staged edits | Generated files wait for review before being applied when auto accept is off |
| Accept / reject | Apply or discard generated edit batches |
| Undo / redo | Reverse or restore accepted batches while the server is running |
| Auto accept | Automatically apply prepared edits when enabled |
| Auto Pilot | More autonomous edit, check, and repair flow for trusted workspaces |
| Plan panel | Inspect phases, files, workers, and pending changes |
| Safe command policy | Allows common project checks while blocking risky paths |
| Local vault memory | Obsidian-style notes, chunks, graph, search, and quality signals |
| OpenAI-compatible API | Local `/v1/models` and `/v1/chat/completions` endpoints |

## Interfaces

GCode can be used in three ways:

| Interface | Start command | Best for |
| --- | --- | --- |
| GCode Terminal | `gcode terminal` | Keyboard-first coding sessions with saved session resume |
| GCode Desktop | `gcode desktop` | Local desktop window with the web UI |
| Web UI | `python app.py` | Browser-based use at `localhost:5000` |

The default `gcode` command opens whichever interface you selected in setup.

Launcher commands:

```cmd
gcode                 :: open the saved default interface
gcode setup           :: run the setup wizard again
gcode terminal        :: open the terminal interface once
gcode terminal --resume <id> :: resume a saved terminal session
gcode resume <id>     :: shortcut for terminal resume
gcode desktop         :: open the desktop interface once
gcode web             :: start the browser web server once
gcode set terminal    :: make Terminal the default
gcode set desktop     :: make Desktop the default
gcode status          :: show launcher configuration
```

The launcher configuration is stored at:

```text
%APPDATA%\GCode\config.json
```

## Model Tiers

GCode exposes four UI-facing tiers:

| Tier | Intended use |
| --- | --- |
| `GaziGPT` | Fast general chat |
| `GaziGPT Thinking` | General chat with stronger planning instructions |
| `GaziGPT Extended` | Multi-stage coding workflow |
| `GaziGPT Hyper` | Stronger coding route with provider fallback |

Provider routing lives in `agent.py`. Availability and quality depend on the installed provider packages and the remote services they can reach.

All tiers are instructed to answer in English by default unless the user asks for another language.

## Effort Levels

The effort selector changes how hard GCode pushes planning, validation, and repair:

| Effort | Behavior |
| --- | --- |
| `no` | Short path, minimal extra analysis |
| `low` | Light planning and basic checks |
| `medium` | Balanced planning, implementation, and validation |
| `high` | Deeper architecture review and stricter quality gates |
| `xhigh` | Most aggressive planning, review, and auto-fix behavior |

This is an application-level control. It does not guarantee a native reasoning-token feature from every upstream provider.

## Security Modes

GCode runs locally and can touch real files in your workspace, so the safety model is intentionally visible.

| Mode | Behavior |
| --- | --- |
| Ask every step | Conservative mode; confirmation is preferred |
| Safe | Allows known safe project commands |
| Full access | More permissive, while still checking project paths |

The command tool is designed around common development checks, such as:

```text
python app.py
python -m compileall
python -m py_compile file.py
flask run
node --check file.js
npm run build
npm test
pytest
```

GCode is not a hardened sandbox. Use it in a workspace you trust.

## Local Memory

GCode includes an internal Obsidian-style vault layer in `obsidian_vault/`.

It is used for:

- local project notes
- chunked source summaries
- contract memory
- context search
- relationship graph data
- quality signals

Runtime memory and chat data are stored locally under `vault_data/`, which is intentionally excluded from Git.

## Edit Review

When auto accept is off, generated files are staged before they are written.

Supported actions:

- inspect a staged edit batch
- inspect a staged file
- accept pending edits
- reject pending edits
- undo the latest accepted batch
- redo an undone batch
- open the generated project folder from the UI

Undo and redo state is process-local and resets when the server restarts.

## API Highlights

Selected local endpoints:

| Endpoint | Purpose |
| --- | --- |
| `POST /api/chat/stream` | Main SSE chat stream |
| `GET /api/chats/sync` | Load the internal chat store |
| `PUT /api/chats/sync` | Save the internal chat store |
| `POST /api/edits/accept` | Accept a staged edit batch |
| `POST /api/edits/reject` | Reject a staged edit batch |
| `POST /api/edits/undo` | Undo the latest accepted batch |
| `POST /api/edits/redo` | Redo an undone batch |
| `GET /api/edits/<plan_id>` | Inspect an edit batch |
| `GET /api/edits/<plan_id>/file?path=...` | Read a staged file |
| `POST /api/open-folder` | Open a generated project folder |
| `GET /api/vault/dashboard` | Internal vault overview |
| `POST /api/vault/search` | Search local vault context |
| `GET /api/vault/graph` | Return the vault relationship graph |
| `GET /api/vault/quality` | Return vault quality signals |
| `GET /v1/models` | OpenAI-compatible model listing |
| `POST /v1/chat/completions` | OpenAI-compatible chat endpoint |

The default local API token is controlled by `GAZIGPT_API_KEY`. If it is not set, the app falls back to `gazigpt`.

## Project Layout

```text
GCode/
|-- agent.py                  Main agent, model routing, coding pipeline, validators
|-- app.py                    Flask server, SSE stream, edit APIs, command APIs
|-- async_orchestrator.py     Async orchestration helpers
|-- contract_memory.py        Contract extraction and project memory wrapper
|-- gcode_config.py           Persistent launcher configuration
|-- gcode_launcher.py         Windows command dispatcher
|-- gcode.cmd                 Windows command launcher
|-- GCode Setup.vbs           Silent double-click setup launcher
|-- install-gcode-command.cmd Double-clickable launcher installer
|-- install-gcode-command.ps1 Adds this folder to the user PATH
|-- main.py                   Optional pywebview desktop launcher
|-- setup_wizard.py           Graphical setup wizard
|-- terminal_ui.py            Keyboard-first terminal interface
|-- requirements.txt          Python dependencies
|-- tier_router.py            Tier routing and provider helpers
|-- obsidian_vault/           Vault, chunk, graph, quality, and contract modules
|-- static/
|   |-- index.html            Main UI shell
|   |-- css/style.css         UI styling
|   |-- js/app.js             Frontend state, streaming, edit panel, chat client
|   `-- uploads/.gitkeep      Runtime uploads placeholder
`-- tools/
    |-- file_manager.py       File read/write/delete/list tool
    |-- shell_executor.py     Command execution tool
    |-- tool_manager.py       Tool registry
    `-- generate_image.py     Image generation URL helper
```

## Requirements

- Python 3.11 or newer is recommended.
- A modern browser is required for the web UI.
- Node.js is optional, but useful for checking generated JavaScript projects.
- `pywebview` is used by the optional desktop wrapper.

Install dependencies with:

```bash
pip install -r requirements.txt
```

## Development Checks

Useful checks before committing:

```bash
python -m compileall agent.py app.py tools obsidian_vault
node --check static/js/app.js
```

If you generate or test projects inside this repository, remove those temporary outputs before publishing a clean source snapshot.

## Troubleshooting

### The model returns short or weak code

Try `GaziGPT Hyper` with `high` or `xhigh` effort. Keep the request specific and ask for a concrete file tree when you want a full project.

### A generated project does not appear on disk

Check whether auto accept is off. Pending edits stay in the plan panel until accepted.

### A command did not run

Check the selected security mode. Some commands require Safe or Full access, and pending edits may need to be accepted before checks make sense.

### The edit panel shows zero stats

The UI calculates diff stats from staged and accepted edit payloads. If a provider sends unusual output, the file may still appear while line totals stay conservative.

## Current Status

GCode is an early local AI IDE. The main local workflows are implemented, including browser chat, terminal chat, staged edits, undo/redo, Auto Pilot, safe command execution, internal vault memory, and the Windows launcher.

Known limitations:

- Provider availability depends on third-party packages and remote provider behavior.
- Validation is practical, but it is not a formal verifier.
- Undo/redo state resets when the server restarts.
- There is no full MSI/EXE installer yet.
- The project currently has no explicit `LICENSE` file.

## Contributing

Useful areas for improvement:

- stronger tests around edit staging and undo/redo
- more deterministic validation for generated apps
- cleaner provider configuration
- focused UI tests for the plan panel
- packaged desktop builds
- a proper license file before broad distribution

Keep the project local-first, transparent, and honest about what has actually been validated.

## License

No explicit license file is included in this snapshot. Add a `LICENSE` file before distributing the project publicly.

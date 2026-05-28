# GCode AI IDE

<p align="center">
  <img alt="GCode AI IDE" src="https://img.shields.io/badge/GCode-AI%20IDE-7c3aed?style=for-the-badge">
  <img alt="Local first" src="https://img.shields.io/badge/Local--First-0ea5e9?style=for-the-badge">
  <img alt="Flask" src="https://img.shields.io/badge/Backend-Flask-111827?style=for-the-badge&logo=flask">
  <img alt="Vanilla JS" src="https://img.shields.io/badge/Frontend-Vanilla%20JS-facc15?style=for-the-badge&logo=javascript&logoColor=111827">
  <img alt="License" src="https://img.shields.io/badge/License-See%20Repository-64748b?style=for-the-badge">
</p>

GCode is a local AI coding workspace with a browser-based IDE, staged file edits, plan tracking, controlled command execution, and validation loops for generated projects.

It is built for long coding sessions where an assistant should do more than answer in chat: it should plan the change, create a real file tree, stage edits, show the diff, run safe checks, and give you a clean summary without dumping huge code blocks into the conversation.

GCode is not a hosted SaaS product. It is a local developer tool intended to run on a trusted machine.

## Quick Start

Clone or download the repository, then run these commands from the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
install-gcode-command.cmd
```

After the graphical setup wizard finishes, open a new Command Prompt and run:

```cmd
gcode
```

Choose **GCode Terminal** if you want a keyboard-first CLI experience, or **GCode Desktop** if you want the desktop window. You can change this later with:

```cmd
gcode setup
```

For the browser UI without installing the command launcher:

```powershell
python app.py
```

Then open `http://localhost:5000`.

## What Makes It Different

GCode combines a chat interface with an edit review workflow:

- It keeps conversations in an internal server-side chat store.
- It stages generated file changes before applying them when auto accept is off.
- It can apply, reject, undo, and redo edit batches.
- It has an Auto Pilot mode for more autonomous safe edits and checks.
- It keeps full generated code out of the chat body and puts it in the edit panel instead.
- It can open the generated project folder from the UI.
- It includes a coding pipeline that plans files, runs sequential file workers, validates output, and attempts repairs when needed.

## Current Status

GCode is usable as an early local AI IDE. The main workflows are implemented and have been exercised locally:

| Area | Status |
| --- | --- |
| Local chat UI | Implemented |
| Internal chat persistence | Implemented with `vault_data/chats.json` |
| Plan panel | Implemented |
| Edit preview | Implemented |
| Accept / reject | Implemented |
| Undo / redo | Implemented in server memory |
| Auto accept | Implemented |
| Auto Pilot | Implemented |
| Effort selector | Implemented |
| Safe command policy | Implemented |
| Internal vault memory | Implemented with Obsidian-style notes, chunks, search, graph, and quality endpoints |
| Final validation | Implemented |
| Automatic repair attempts | Implemented |
| Windows `gcode` command launcher | Implemented |
| GCode Terminal interface | Implemented |
| Graphical setup wizard for default interface | Implemented |
| Desktop wrapper | Lightweight `pywebview` launcher |

Known limitations:

- Provider availability depends on the installed backend packages and the remote providers they expose.
- Validation is practical and helpful, but it is not a formal verifier.
- Undo/redo state is process-local and resets when the server restarts.
- There is no full MSI/EXE installer yet; a Windows command-line launcher is included.

## Interface

The app is served from `static/index.html` and communicates with the Flask backend through JSON and SSE endpoints.
The interface uses the full window height directly, without a desktop-style menu strip above the workspace.

Main UI controls:

| Control | Purpose |
| --- | --- |
| Model selector | Switch between Core, Thinking, Extended, and Hyper modes |
| Effort selector | Choose `no`, `low`, `medium`, `high`, or `xhigh` orchestration effort |
| Security selector | Choose how much the backend may do without asking |
| Auto accept | Automatically apply prepared edit batches when allowed |
| Auto Pilot | Allow a more autonomous flow for safe edits, commands, and repairs |
| Plan panel | Review phases, files, workers, and staged changes |

## Model Tiers

GCode exposes four UI-facing tiers:

| Tier | Intended Use |
| --- | --- |
| `GaziGPT` | Fast general chat |
| `GaziGPT Thinking` | General chat with stronger planning instructions |
| `GaziGPT Extended` | Multi-stage coding pipeline |
| `GaziGPT Hyper` | Stronger coding route with provider fallback |

Provider routing lives in `agent.py`. The current code supports provider fallback behavior, but real quality and availability depend on the installed provider stack and remote service behavior.

All tiers are instructed to answer in English by default unless the user explicitly asks for another language.

## Reasoning Effort

The effort selector changes orchestration behavior and prompt strictness:

| Effort | Behavior |
| --- | --- |
| `no` | Short path, minimal extra analysis |
| `low` | Light planning and basic checks |
| `medium` | Balanced planning, implementation, and validation |
| `high` | Deeper architecture review and stricter quality gates |
| `xhigh` | Most aggressive planning, review, and auto-fix behavior |

This is an application-level control. It does not guarantee a native reasoning-token feature from every upstream provider.

## Coding Pipeline

For coding tasks, Extended and Hyper can run a staged pipeline:

1. Clarify the request.
2. Generate ideas.
3. Search available context.
4. Plan folders and files.
5. Build per-file prompts.
6. Generate each file sequentially.
7. Validate each stage.
8. Review cross-file consistency.
9. Stage edits.
10. Apply edits when allowed.
11. Run safe commands when allowed.
12. Perform final validation.
13. Trigger auto-fix when recoverable issues are found.

The pipeline intentionally avoids parallel LLM calls. This keeps provider usage predictable and prevents overlapping file workers.

## Internal Vault Memory

GCode includes an internal Obsidian-style vault layer used for local context, project notes, chunked source summaries, contract memory, graph views, and quality checks. The vault is implemented in `obsidian_vault/` and is accessed through local Flask endpoints; it is not connected to an external hosted note service by default.

The vault system is designed to help long coding sessions keep useful project context available without sending the entire repository into every prompt.

## Edit Review

GCode keeps edit batches in memory while the server is running.

Supported edit actions:

- Accept pending edits
- Reject pending edits
- Undo the latest accepted batch
- Redo an undone batch
- Inspect a staged file
- Open the project folder from the UI

When auto accept is off, generated files wait in the plan panel instead of being written immediately.

## Command Execution

The backend includes a command execution tool with a safety layer. Examples of command shapes GCode is designed to handle:

```text
python app.py
python main.py
python -m compileall
python -m py_compile file.py
flask run
node --check file.js
npm run dev
npm run build
npm run start
npm test
pytest
```

Commands still run on your local machine. Use GCode in a trusted workspace.

## Security Modes

| Mode | Behavior |
| --- | --- |
| Ask every step | Conservative mode; user confirmation is preferred |
| Safe | Allows known safe project commands |
| Full access | More permissive, while still blocking writes outside the project folder |

File paths are normalized and checked before file operations are applied.

## Project Layout

```text
GCode/
|-- agent.py                 Main AI agent, model routing, coding pipeline, validators
|-- app.py                   Flask server, SSE stream, edit APIs, command APIs
|-- async_orchestrator.py    Async orchestration helpers
|-- contract_memory.py       Contract extraction and project memory wrapper
|-- gcode_config.py          Persistent launcher configuration
|-- gcode_launcher.py        Windows command dispatcher for Terminal/Desktop/Web
|-- gcode.cmd                Windows command launcher
|-- GCode Setup.vbs          Silent double-click launcher for the setup wizard
|-- install-gcode-command.cmd Double-clickable launcher installer
|-- install-gcode-command.ps1 Adds this folder to the user PATH
|-- main.py                  Optional pywebview desktop launcher
|-- setup_wizard.py          Graphical installer and default-interface wizard
|-- terminal_ui.py           Keyboard-first terminal interface with pasted image tokens
|-- requirements.txt         Python dependencies
|-- tier_router.py           Tier routing and provider helpers
|-- obsidian_vault/          Vault, chunk, graph, quality, and contract source modules
|-- static/
|   |-- index.html           Main UI shell
|   |-- css/style.css        UI styling
|   |-- js/app.js            Frontend state, streaming, edit panel, internal chat store client
|   `-- uploads/.gitkeep     Runtime uploads folder placeholder
`-- tools/
    |-- file_manager.py      File write/read/delete/list tool
    |-- shell_executor.py    Command execution tool
    |-- tool_manager.py      Tool registry
    `-- generate_image.py    Image generation URL helper
```

## Requirements

- Python 3.11 or newer is recommended.
- Node.js is optional, but useful for checking generated JavaScript projects.
- A modern browser is required for the web UI.

Install Python dependencies from `requirements.txt`.

## Installation

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it on Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Activate it on macOS or Linux:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## Windows Command Launcher

GCode includes a small Windows launcher so you can start the project from Command Prompt:

```cmd
gcode
```

On first setup, a graphical wizard requires one default-interface choice:

| Choice | What `gcode` opens |
| --- | --- |
| GCode Terminal | A keyboard-first terminal interface with a pinned input bar, scrollable output, slash-command palette, `/model`, and clipboard image paste |
| GCode Desktop | The `pywebview` desktop window from `main.py` |

Install the command once by double-clicking either launcher:

```text
GCode Setup.vbs
```

or:

```text
install-gcode-command.cmd
```

Or run it from Command Prompt:

```cmd
install-gcode-command.cmd
```

PowerShell users can also run the underlying script directly, but the normal installer opens the graphical wizard:

```powershell
.\install-gcode-command.ps1
```

The installer adds the project folder to your user `PATH` and opens `setup_wizard.py`. Open a new Command Prompt after installing, then run `gcode` from anywhere. The launcher uses `.venv\Scripts\python.exe` automatically when it exists; otherwise it falls back to `python`.

If Windows asks which app should open a `.ps1` file, use `install-gcode-command.cmd` instead. The `.cmd` wrapper launches PowerShell with the correct execution policy for this installer.

Launcher commands:

```cmd
gcode                 :: open the saved default interface
gcode setup           :: run the setup wizard again
gcode terminal        :: open the terminal interface once
gcode desktop         :: open the desktop interface once
gcode web             :: start the browser web server once
gcode set terminal    :: make Terminal the default
gcode set desktop     :: make Desktop the default
gcode status          :: show the saved launcher configuration
```

The launcher configuration is stored outside the repository at:

```text
%APPDATA%\GCode\config.json
```

`GCode Setup.vbs` opens the graphical wizard without a console window. `install-gcode-command.cmd` opens the same graphical wizard but keeps a console visible so startup errors are easy to read.

GCode Terminal has a dark command-console interface with a status header, setup hints, a project trust notice, a bottom-pinned prompt bar, PageUp/PageDown scrollback, and a slash-command palette. Type `/` to see matching commands, press Tab to complete the first match, and use commands such as `/model`, `/setup`, `/desktop`, `/web`, `/directory`, `/run`, `/compress`, `/stats`, `/tasks`, `/clear`, and `/exit`.

The terminal model route can be changed with `/model core`, `/model thinking`, `/model extended`, or `/model hyper`.

Clipboard images can be pasted into the terminal with `Ctrl+V`. They appear as a single `[pasted image]` token in the input bar, are saved under `vault_data/terminal_uploads`, and can be removed in one step with Backspace or Delete.

## Run the Web UI

```bash
python app.py
```

Open:

```text
http://localhost:5000
```

## Optional Desktop Launcher

If `pywebview` is installed, you can start the lightweight desktop wrapper:

```bash
python main.py
```

The web UI remains the primary interface.

## API Highlights

GCode includes local endpoints for chat, edit review, permissions, image proxying, and OpenAI-compatible access.

Selected endpoints:

| Endpoint | Purpose |
| --- | --- |
| `POST /api/chat/stream` | Main SSE chat stream |
| `GET /api/chats/sync` | Load the internal chat store |
| `PUT /api/chats/sync` | Save the internal chat store |
| `POST /api/edits/accept` | Accept a staged edit batch |
| `POST /api/edits/reject` | Reject a staged edit batch |
| `POST /api/edits/undo` | Undo the latest accepted batch |
| `POST /api/edits/redo` | Redo the latest undone batch |
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

## Local Data

Runtime data is intentionally kept out of Git:

- `.env`
- Python caches
- `vault_data/`
- uploaded files under `static/uploads/`
- generated smoke-test projects
- temporary patch scripts

Chat history is stored in `vault_data/chats.json`. This is a local JSON store, not an external hosted database.

## Development Checks

Useful checks before committing:

```bash
python -m compileall agent.py app.py tools obsidian_vault
node --check static/js/app.js
```

If you generate or test projects inside the repository, remove them before publishing a clean source snapshot.

## Troubleshooting

### The model returns short or weak code

Try `GaziGPT Hyper` with `high` or `xhigh` effort, keep the request specific, and ask for a concrete file tree. Provider behavior can still vary.

### A generated project does not appear on disk

Check whether auto accept is off. Pending edits stay in the plan panel until accepted.

### A command did not run

Check the security mode. Some commands require Safe or Full access, and pending edits may need to be accepted before commands run.

### The edit panel shows zero stats

The UI calculates diff stats from staged/accepted edit payloads. If a provider sends unusual output, the panel may still show the file while line totals are conservative.

## Contributing

Good improvements for this codebase:

- More deterministic validation for generated frontend/backend projects
- Stronger test coverage around edit staging and undo/redo
- Cleaner provider configuration
- More focused UI tests for the plan panel
- Packaged desktop builds

Keep the project local-first, transparent, and honest about what is validated.

## License

No explicit license file is included in this snapshot. Add a `LICENSE` file before distributing the project publicly.

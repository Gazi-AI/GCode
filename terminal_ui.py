import os
import contextlib
import getpass
import io
import re
import shutil
import subprocess
import sys
import textwrap
import time
import json
import uuid
from pathlib import Path

from gcode_config import choose_default_interface, get_default_interface, load_config, set_default_interface


ROOT = Path(__file__).resolve().parent
TERMINAL_UPLOAD_DIR = ROOT / "vault_data" / "terminal_uploads"
TERMINAL_SESSION_STORE_PATH = ROOT / "vault_data" / "terminal_sessions.json"
WEB_CHAT_STORE_PATH = ROOT / "vault_data" / "chats.json"
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
SAFE_RUN_PREFIXES = (
    "python -m compileall",
    "python -m py_compile",
    "python app.py",
    "python main.py",
    "node --check",
    "npm run build",
    "npm run dev",
    "npm run start",
    "npm test",
    "pytest",
)

SLASH_COMMANDS = [
    ("about", "Show version and provider info"),
    ("auth", "Show local identity and session status"),
    ("cd", "Change directory inside this project"),
    ("clear", "Clear the screen and start a fresh terminal view"),
    ("compress", "Compress terminal chat context by keeping recent turns"),
    ("desktop", "Launch the GCode Desktop window"),
    ("directory", "Show and manage the current workspace directory"),
    ("exit", "Exit the CLI"),
    ("footer", "Show footer/statusline configuration"),
    ("help", "Show this command list"),
    ("history", "Show the current chat turn count"),
    ("ls", "List files inside the project"),
    ("model", "Manage terminal model configuration"),
    ("models", "Show available GCode model tiers"),
    ("mode", "Show the saved default interface"),
    ("new", "Start a new terminal chat session"),
    ("permissions", "Show workspace trust and command policy"),
    ("pwd", "Show the current project directory"),
    ("quit", "Exit the CLI"),
    ("read", "Preview a text file inside the project"),
    ("resume", "Resume a saved session. Usage: /resume <session-id>"),
    ("run", "Run a safe local command from the current directory"),
    ("sessions", "List saved terminal and web chat sessions"),
    ("setmode", "Change the saved default interface"),
    ("setup", "Change what plain 'gcode' opens"),
    ("stats", "Check session stats. Usage: /stats [session|model|tools]"),
    ("status", "Show launcher and workspace status"),
    ("tasks", "Toggle background tasks view"),
    ("upgrade", "Show local edition/provider notes"),
    ("web", "Start the browser web server"),
]

TERMINAL_MODEL_OPTIONS = {
    "core": ("GaziGPT", "openai-fast", "Fast general chat"),
    "thinking": ("GaziGPT Thinking", "openai-fast", "Planning-focused assistant"),
    "extended": ("GaziGPT Extended", "extended", "Multi-stage coding pipeline route"),
    "hyper": ("GaziGPT Hyper", "hyper", "Strongest coding route with fallback"),
}


class C:
    reset = "\033[0m"
    bold = "\033[1m"
    dim = "\033[2m"
    red = "\033[31m"
    green = "\033[32m"
    yellow = "\033[33m"
    blue = "\033[34m"
    magenta = "\033[35m"
    cyan = "\033[36m"
    white = "\033[97m"
    gray = "\033[90m"
    bg_input = "\033[48;5;236m"
    bg_panel = "\033[48;5;232m"


def enable_ansi():
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def color(text, code):
    return f"{code}{text}{C.reset}"


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def terminal_width(default=120):
    return max(72, shutil.get_terminal_size((default, 24)).columns)


def terminal_size(default=(120, 32)):
    size = shutil.get_terminal_size(default)
    return max(72, size.columns), max(24, size.lines)


def strip_ansi(text):
    return ANSI_RE.sub("", str(text))


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_session_id():
    return "t_" + uuid.uuid4().hex[:12]


def terminal_message_title(messages):
    for message in messages or []:
        if message.get("role") == "user":
            content = str(message.get("content", "")).strip().replace("\n", " ")
            if content:
                return content[:60] + ("..." if len(content) > 60 else "")
    return "New terminal session"


def clean_messages(messages):
    clean = []
    for message in messages or []:
        role = message.get("role")
        content = message.get("content", "")
        if role in {"user", "assistant", "system"} and isinstance(content, str):
            clean.append({"role": role, "content": content})
    return clean


def load_terminal_store():
    if not TERMINAL_SESSION_STORE_PATH.exists():
        return {"version": 1, "current_session_id": None, "sessions": {}}
    try:
        data = json.loads(TERMINAL_SESSION_STORE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "current_session_id": None, "sessions": {}}
    sessions = data.get("sessions", {}) if isinstance(data, dict) else {}
    if not isinstance(sessions, dict):
        sessions = {}
    current_session_id = data.get("current_session_id") if isinstance(data, dict) else None
    if current_session_id not in sessions:
        current_session_id = None
    return {"version": 1, "current_session_id": current_session_id, "sessions": sessions}


def save_terminal_store(store):
    TERMINAL_SESSION_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    clean_store = {
        "version": 1,
        "current_session_id": store.get("current_session_id"),
        "sessions": store.get("sessions", {}),
    }
    tmp = TERMINAL_SESSION_STORE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(clean_store, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(TERMINAL_SESSION_STORE_PATH)


def save_terminal_session(session_id, messages):
    messages = clean_messages(messages)
    if not messages:
        return False
    store = load_terminal_store()
    sessions = store["sessions"]
    existing = sessions.get(session_id, {})
    timestamp = now_iso()
    sessions[session_id] = {
        "id": session_id,
        "kind": "terminal",
        "title": terminal_message_title(messages),
        "created_at": existing.get("created_at") or timestamp,
        "updated_at": timestamp,
        "messages": messages,
    }
    store["current_session_id"] = session_id
    save_terminal_store(store)
    return True


def load_web_chat_store():
    if not WEB_CHAT_STORE_PATH.exists():
        return {"chats": {}}
    try:
        data = json.loads(WEB_CHAT_STORE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"chats": {}}
    chats = data.get("chats", {}) if isinstance(data, dict) else {}
    return {"chats": chats if isinstance(chats, dict) else {}}


def get_saved_session(session_id):
    store = load_terminal_store()
    session = store["sessions"].get(session_id)
    if session:
        return "terminal", session
    chat = load_web_chat_store()["chats"].get(session_id)
    if chat:
        return "web", chat
    return None, None


def list_saved_sessions(limit=20):
    rows = []
    for session in load_terminal_store()["sessions"].values():
        rows.append({
            "id": session.get("id", ""),
            "kind": "terminal",
            "title": session.get("title") or terminal_message_title(session.get("messages", [])),
            "updated_at": session.get("updated_at") or session.get("created_at") or "",
            "count": len(session.get("messages", [])),
        })
    for chat in load_web_chat_store()["chats"].values():
        rows.append({
            "id": chat.get("id", ""),
            "kind": "web",
            "title": chat.get("title") or terminal_message_title(chat.get("messages", [])),
            "updated_at": chat.get("updated_at") or chat.get("created_at") or "",
            "count": len(chat.get("messages", [])),
        })
    rows.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return rows[:limit]


def print_sessions(limit=20):
    rows = list_saved_sessions(limit=limit)
    print(color("Saved sessions", C.bold + C.cyan))
    if not rows:
        print("  No saved sessions yet.")
        print("")
        return
    for row in rows:
        session_id = str(row["id"])
        title = str(row["title"] or "Untitled").replace("\n", " ")
        if len(title) > 52:
            title = title[:49] + "..."
        print(
            f"  {color(session_id.ljust(16), C.green)} "
            f"{color(row['kind'].ljust(8), C.magenta)} "
            f"{str(row['count']).rjust(3)} msg  {title}"
        )
    print("")
    print("  Resume with: /resume <session-id>")
    print("")


def print_box(title, body, border=C.yellow, text=C.text if hasattr(C, "text") else C.reset):
    width = min(terminal_width() - 2, max(72, len(title) + 8))
    print(color("+" + "-" * (width - 2) + "+", border))
    print(color("| ", border) + color(title.ljust(width - 4), C.bold + border) + color(" |", border))
    for raw_line in textwrap.wrap(body, width=width - 4) or [""]:
        print(color("| ", border) + raw_line.ljust(width - 4) + color(" |", border))
    print(color("+" + "-" * (width - 2) + "+", border))


def info_line(text):
    print(color("[i]", C.cyan + C.bold) + " " + color(text, C.yellow))


def print_banner():
    enable_ansi()
    clear_screen()
    user = getpass.getuser()
    default_mode = get_default_interface(load_config()) or "not configured"
    if os.name == "nt":
        os.system(f"title Ready ({user})")
    logo = [
        "     ##",
        "    ##  ",
        "   ##   ",
        "  ##  ##",
    ]
    header = [
        color(f"Ready ({user})", C.bold) + color("  /status", C.gray),
        color("Signed in with Local Workspace", C.bold) + color("  /auth", C.gray),
        color("Plan: GCode AI IDE", C.bold) + color(f"  default={default_mode}  /setup", C.gray),
    ]
    for i, line in enumerate(header):
        logo_line = logo[i] if i < len(logo) else ""
        logo_color = [C.blue, C.cyan, C.magenta, C.yellow][i % 4]
        print(color(logo_line.ljust(12), logo_color) + line)
    print("")
    print_box(
        "GCode Terminal is ready",
        "This interface is the terminal face of GCode. Use slash commands for local actions, "
        "or type a normal message to talk to the AI. It can inspect project files, run safe "
        "commands, launch Desktop, and change setup without leaving the shell.",
        border=C.yellow,
    )
    print("")
    info_line("Project root is trusted for this local workspace.")
    info_line("Use /help to see commands, /desktop to open the GUI, and /exit to quit.")
    info_line("Use @path/to/file in your prompt when you want to reference a project file.")
    info_line("Use /permissions to review local command and file access behavior.")
    print("")
    print("")


def print_help():
    print(color("Slash commands", C.bold + C.cyan))
    for command, description in SLASH_COMMANDS:
        print(f"  {color(('/' + command).ljust(28), C.green)} {description}")
    print(f"  {color('/q'.ljust(28), C.green)} Exit GCode Terminal")
    print("")
    print(color("Input shortcuts", C.bold + C.cyan))
    print(f"  {color('Ctrl+V'.ljust(28), C.green)} Paste text or attach a clipboard image as one token")
    print(f"  {color('Backspace/Delete'.ljust(28), C.green)} Remove pasted image tokens in one step")
    print(f"  {color('Left/Right'.ljust(28), C.green)} Move across text and pasted image tokens")
    print("")


def print_status(cwd, messages):
    print(color("Status", C.bold + C.cyan))
    print(f"  User: {getpass.getuser()}")
    print(f"  Root: {ROOT}")
    print(f"  CWD:  {cwd}")
    print(f"  Default interface: {get_default_interface(load_config()) or 'not configured'}")
    print(f"  Chat turns: {len(messages)}")
    print("")


def print_auth():
    print(color("Signed in with Local Workspace", C.bold + C.green))
    print("  Auth mode: local machine")
    print("  Account: current Windows user")
    print("  Cloud login: not required for the terminal shell")
    print("")


def print_upgrade():
    print(color("GCode AI IDE", C.bold + C.magenta))
    print("  This is a local developer build, not a hosted subscription product.")
    print("  Provider quality depends on the configured backend routes in agent.py.")
    print("  Use the web UI model selector for Core, Thinking, Extended, and Hyper.")
    print("")


def print_permissions():
    print_box(
        "Workspace permissions",
        "This terminal trusts only the GCode project folder for file operations. "
        "Commands in the safe list run directly; other commands ask before execution. "
        "Generated code should be reviewed before running when it touches dependencies, servers, or external services.",
        border=C.cyan,
    )
    print("")


def print_models():
    rows = [
        ("GaziGPT", "Fast general chat"),
        ("GaziGPT Thinking", "Planning-focused assistant"),
        ("GaziGPT Extended", "Multi-stage coding pipeline"),
        ("GaziGPT Hyper", "Strongest coding route with fallback"),
    ]
    print(color("Model tiers", C.bold + C.cyan))
    for name, desc in rows:
        print(f"  {color(name.ljust(20), C.magenta)} {desc}")
    print("")


def print_model_config(current="core"):
    print(color("Model configuration", C.bold + C.cyan))
    print(f"  Current terminal model: {color(TERMINAL_MODEL_OPTIONS.get(current, TERMINAL_MODEL_OPTIONS['core'])[0], C.magenta)}")
    print("")
    print("  Usage:")
    print(f"    {color('/model'.ljust(18), C.green)} show this panel")
    print(f"    {color('/model core'.ljust(18), C.green)} fast general chat")
    print(f"    {color('/model thinking'.ljust(18), C.green)} extra planning instructions")
    print(f"    {color('/model extended'.ljust(18), C.green)} extended coding route")
    print(f"    {color('/model hyper'.ljust(18), C.green)} hyper coding route")
    print("")
    for key, (label, _override, desc) in TERMINAL_MODEL_OPTIONS.items():
        marker = "*" if key == current else " "
        print(f"  {marker} {color(key.ljust(10), C.magenta)} {label.ljust(18)} {desc}")
    print("")


def print_directory(cwd):
    print(color("Directory", C.bold + C.cyan))
    print(f"  Root: {ROOT}")
    print(f"  CWD:  {cwd}")
    print("  Use /cd <path>, /pwd, /ls [path], and /read <file> to navigate this workspace.")
    print("")


def print_footer_info():
    print(color("Footer", C.bold + C.cyan))
    print("  The footer is pinned at the bottom of the terminal.")
    print("  It shows shortcut hints, paste status, loading states, and scroll help.")
    print("")


def print_tasks_info():
    print(color("Tasks", C.bold + C.cyan))
    print("  Terminal background task view is reserved for future long-running jobs.")
    print("  Current safe commands run through /run and report output inline.")
    print("")


def render_input_bar(buffer, rel):
    width = terminal_width()
    label = "> "
    placeholder = "Type your message or @path/to/file"
    prefix = label
    content = buffer if buffer else placeholder
    content_color = C.white if buffer else C.gray
    visible_len = len(prefix) + len(content)
    if visible_len > width - 1:
        keep = max(10, width - len(prefix) - 4)
        content = "..." + content[-keep:]
        visible_len = len(prefix) + len(content)
    pad = " " * max(0, width - visible_len - 1)
    line = (
        "\r"
        + C.bg_input
        + C.magenta
        + prefix
        + C.bg_input
        + content_color
        + content
        + pad
        + C.reset
    )
    sys.stdout.write(line)
    sys.stdout.flush()


def read_terminal_input(cwd):
    rel = "." if cwd == ROOT else str(cwd.relative_to(ROOT))
    if os.name != "nt" or not sys.stdin.isatty():
        return input(color("> ", C.bold + C.magenta) + color(f"{rel} ", C.gray))

    import msvcrt

    buffer = ""
    render_input_bar(buffer, rel)
    while True:
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            msvcrt.getwch()
            continue
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch == "\r":
            sys.stdout.write("\n")
            sys.stdout.flush()
            return buffer
        if ch in ("\b", "\x7f"):
            buffer = buffer[:-1]
            render_input_bar(buffer, rel)
            continue
        if ch == "\x1b":
            buffer = ""
            render_input_bar(buffer, rel)
            continue
        if ch >= " ":
            buffer += ch
            render_input_bar(buffer, rel)


def header_lines(width, cwd, messages, session_id="", selected_model="core"):
    user = getpass.getuser()
    default_mode = get_default_interface(load_config()) or "not configured"
    root_label = str(ROOT)
    if len(root_label) > width - 28:
        root_label = "..." + root_label[-(width - 31):]
    model_label = TERMINAL_MODEL_OPTIONS.get(selected_model, TERMINAL_MODEL_OPTIONS["core"])[0]
    session_text = session_id or "unsaved"
    lines = [
        color("GCode Terminal", C.bold + C.green)
        + color(f"  Ready ({user})", C.bold)
        + color(f"  session={session_text}", C.gray),
        color(f"Root: {root_label}", C.gray),
        color(f"Model: {model_label}", C.magenta)
        + color(f"  default={default_mode}", C.gray)
        + color("  /sessions  /resume <id>  /help", C.gray),
        "",
    ]
    return lines


def wrap_history_lines(lines, width):
    wrapped = []
    for line in lines:
        clean = strip_ansi(line)
        if not clean:
            wrapped.append("")
            continue
        for part in textwrap.wrap(clean, width=max(20, width - 2), replace_whitespace=False, drop_whitespace=False):
            wrapped.append(part)
    return wrapped


class TerminalTUI:
    def __init__(self, resume_session_id=None):
        self.cwd = ROOT
        self.messages = []
        self.agent = None
        self.buffer = ""
        self.cursor = 0
        self.image_attachments = {}
        self.selected_model = "core"
        self.current_session_id = new_session_id()
        self.history = []
        self.scroll_offset = 0
        self.running = True
        self.last_width = 0
        self.last_height = 0
        self.last_suggestion_count = 0
        self.input_row = 1
        self.footer_row = 1
        if resume_session_id:
            self.resume_session(resume_session_id, announce=False)
        else:
            self.append(f"New session: {self.current_session_id}")
            self.append("Use /sessions to list chats, /resume <id> to continue one, or /help for commands.")
            self.append("")

    def append(self, text=""):
        for line in str(text).splitlines() or [""]:
            self.history.append(strip_ansi(line))
        self.scroll_offset = 0

    def append_block(self, title, body):
        self.append(f"{title}")
        for line in textwrap.wrap(str(body), width=100):
            self.append(f"  {line}")
        self.append("")

    def save_current_session(self):
        return save_terminal_session(self.current_session_id, self.messages)

    def rebuild_history_from_messages(self, intro=""):
        self.history = []
        if intro:
            self.append(intro)
            self.append("")
        for message in self.messages:
            role = message.get("role")
            content = str(message.get("content", ""))
            if role == "user":
                self.append(f"> {content}")
            elif role == "assistant":
                self.append_block("GCode", content)

    def resume_session(self, session_id, announce=True):
        kind, session = get_saved_session(session_id)
        if not session:
            if announce:
                self.append(f"Session not found: {session_id}")
            return False
        self.current_session_id = session_id if kind == "terminal" else new_session_id()
        self.messages = clean_messages(session.get("messages", []))
        if kind == "web":
            self.save_current_session()
            intro = f"Imported web chat {session_id} into terminal session {self.current_session_id}."
        else:
            intro = f"Resumed session {session_id}."
        if announce:
            self.rebuild_history_from_messages(intro)
        else:
            self.rebuild_history_from_messages(intro)
        return True

    def new_session(self):
        self.save_current_session()
        self.current_session_id = new_session_id()
        self.messages = []
        self.history = []
        self.append(f"New session: {self.current_session_id}")
        self.append("Use /sessions to list saved chats.")
        self.append("")

    def marker_spans(self):
        spans = []
        for marker in self.image_attachments:
            start = self.buffer.find(marker)
            if start >= 0:
                spans.append((start, start + len(marker), marker))
        return sorted(spans)

    def span_ending_at(self, index):
        for start, end, marker in self.marker_spans():
            if end == index:
                return start, end, marker
        return None

    def span_starting_at(self, index):
        for start, end, marker in self.marker_spans():
            if start == index:
                return start, end, marker
        return None

    def normalize_cursor(self):
        self.cursor = max(0, min(self.cursor, len(self.buffer)))
        for start, end, _marker in self.marker_spans():
            if start < self.cursor < end:
                self.cursor = end
                break

    def insert_text(self, text):
        if not text:
            return
        text = str(text).replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ")
        self.normalize_cursor()
        self.buffer = self.buffer[:self.cursor] + text + self.buffer[self.cursor:]
        self.cursor += len(text)

    def insert_image_token(self, path):
        image_id = str(int(time.time() * 1000))
        marker = f"[[pasted-image:{image_id}]]"
        self.image_attachments[marker] = {"path": str(path)}
        self.normalize_cursor()
        self.buffer = self.buffer[:self.cursor] + marker + self.buffer[self.cursor:]
        self.cursor += len(marker)

    def delete_before_cursor(self):
        if self.cursor <= 0:
            return
        self.normalize_cursor()
        span = self.span_ending_at(self.cursor)
        if span:
            start, end, marker = span
            self.buffer = self.buffer[:start] + self.buffer[end:]
            self.cursor = start
            self.image_attachments.pop(marker, None)
            return
        self.buffer = self.buffer[:self.cursor - 1] + self.buffer[self.cursor:]
        self.cursor -= 1

    def delete_at_cursor(self):
        if self.cursor >= len(self.buffer):
            return
        self.normalize_cursor()
        span = self.span_starting_at(self.cursor)
        if span:
            start, end, marker = span
            self.buffer = self.buffer[:start] + self.buffer[end:]
            self.image_attachments.pop(marker, None)
            return
        self.buffer = self.buffer[:self.cursor] + self.buffer[self.cursor + 1:]

    def move_cursor_left(self):
        if self.cursor <= 0:
            return
        self.normalize_cursor()
        span = self.span_ending_at(self.cursor)
        self.cursor = span[0] if span else self.cursor - 1

    def move_cursor_right(self):
        if self.cursor >= len(self.buffer):
            return
        self.normalize_cursor()
        span = self.span_starting_at(self.cursor)
        self.cursor = span[1] if span else self.cursor + 1

    def clear_input(self):
        self.buffer = ""
        self.cursor = 0
        self.image_attachments = {}

    def display_buffer(self, raw=None):
        text = self.buffer if raw is None else str(raw)
        for marker in sorted(self.image_attachments, key=len, reverse=True):
            text = text.replace(marker, "[pasted image]")
        return text

    def prompt_buffer(self, raw=None):
        text = self.buffer if raw is None else str(raw)
        for marker, info in self.image_attachments.items():
            text = text.replace(marker, f"[pasted image: {info.get('path', '')}]")
        return text.strip()

    def display_text_and_cursor(self):
        self.normalize_cursor()
        output = []
        cursor_display = 0
        i = 0
        spans = {start: (end, marker) for start, end, marker in self.marker_spans()}
        while i < len(self.buffer):
            if i == self.cursor:
                cursor_display = len("".join(output))
            span = spans.get(i)
            if span:
                end, _marker = span
                output.append("[pasted image]")
                if self.cursor == end:
                    cursor_display = len("".join(output))
                i = end
                continue
            output.append(self.buffer[i])
            i += 1
        if self.cursor == len(self.buffer):
            cursor_display = len("".join(output))
        return "".join(output), cursor_display

    def save_clipboard_image(self):
        try:
            from PIL import ImageGrab
        except Exception:
            return None

        try:
            data = ImageGrab.grabclipboard()
        except Exception:
            return None

        TERMINAL_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 1000:03d}"
        if hasattr(data, "save"):
            path = TERMINAL_UPLOAD_DIR / f"pasted-image-{stamp}.png"
            data.save(path, "PNG")
            return path

        if isinstance(data, list):
            image_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
            for item in data:
                source = Path(str(item))
                if source.is_file() and source.suffix.lower() in image_exts:
                    target = TERMINAL_UPLOAD_DIR / f"pasted-image-{stamp}{source.suffix.lower()}"
                    shutil.copy2(source, target)
                    return target
        return None

    def read_clipboard_text(self):
        try:
            import tkinter as tk

            root = tk.Tk()
            root.withdraw()
            text = root.clipboard_get()
            root.destroy()
            return text
        except Exception:
            return ""

    def paste_from_clipboard(self):
        image_path = self.save_clipboard_image()
        if image_path:
            self.insert_image_token(image_path)
            return "Pasted image attached."

        text = self.read_clipboard_text()
        if text:
            self.insert_text(text)
            return "Text pasted."
        return "Clipboard is empty or unsupported."

    def slash_matches(self):
        text = self.display_buffer()
        if not text.startswith("/"):
            return []
        if " " in text.strip():
            return []
        query = text[1:].lower()
        matches = [(cmd, desc) for cmd, desc in SLASH_COMMANDS if cmd.startswith(query)]
        return matches[:8]

    def slash_suggestion_lines(self, width):
        matches = self.slash_matches()
        if not matches:
            return []
        total = len([cmd for cmd, _desc in SLASH_COMMANDS if cmd.startswith(self.display_buffer()[1:].lower())])
        lines = []
        name_width = min(22, max(12, width // 4))
        for index, (cmd, desc) in enumerate(matches):
            prefix = C.bg_panel if index == 0 else ""
            suffix = C.reset if index == 0 else ""
            command_text = color(cmd.ljust(name_width), C.green + C.bold) if index == 0 else color(cmd.ljust(name_width), C.gray)
            desc_color = C.white if index == 0 else C.gray
            plain_len = name_width + 2 + len(desc)
            pad = " " * max(0, width - plain_len)
            lines.append((prefix + command_text + "  " + color(desc, desc_color) + pad + suffix)[: width + 40])
        lines.append(color(f"({min(len(matches), total)}/{max(total, 1)})", C.gray).ljust(width))
        return lines

    def refresh_input(self):
        if self.buffer.startswith("/") or self.last_suggestion_count:
            self.render()
        else:
            self.render_input_only()

    def accept_first_suggestion(self):
        matches = self.slash_matches()
        if not matches:
            return False
        self.buffer = "/" + matches[0][0] + " "
        self.cursor = len(self.buffer)
        return True

    def terminal_model_override(self):
        if self.selected_model == "thinking":
            return "openai-fast"
        if self.selected_model == "extended" and self.agent is not None:
            return getattr(self.agent, "EXTENDED_MODEL_OVERRIDE", "g4f:OperaAria")
        if self.selected_model == "hyper" and self.agent is not None:
            return getattr(self.agent, "HYPER_MODEL_OVERRIDE", "g4f:Yqcloud")
        if self.selected_model == "extended":
            return "g4f:OperaAria"
        if self.selected_model == "hyper":
            return "g4f:Yqcloud"
        return "openai-fast"

    def terminal_system_prompt(self):
        label = TERMINAL_MODEL_OPTIONS.get(self.selected_model, TERMINAL_MODEL_OPTIONS["core"])[0]
        extra = f"\nTerminal selected model: {label}."
        if self.selected_model == "thinking":
            extra += "\nUse a short private planning pass before answering, then respond directly."
        elif self.selected_model in {"extended", "hyper"}:
            extra += "\nFor coding tasks, be strict about real files, complete implementations, validation, and concise summaries."
        return TERMINAL_SYSTEM_PROMPT + extra

    def handle_model_command(self, arg):
        choice = (arg or "").strip().lower()
        if not choice:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                print_model_config(self.selected_model)
            self.append(output.getvalue().strip())
            return
        aliases = {
            "gazigpt": "core",
            "core": "core",
            "thinking": "thinking",
            "think": "thinking",
            "extended": "extended",
            "ext": "extended",
            "hyper": "hyper",
        }
        selected = aliases.get(choice)
        if not selected:
            self.append("Usage: /model core|thinking|extended|hyper")
            return
        self.selected_model = selected
        label = TERMINAL_MODEL_OPTIONS[selected][0]
        self.append(f"Terminal model set to {label}.")

    def render(self, status=""):
        width, height = terminal_size()
        head = header_lines(width, self.cwd, self.messages, self.current_session_id, self.selected_model)
        suggestions = self.slash_suggestion_lines(width)
        self.last_suggestion_count = len(suggestions)
        input_height = 4 + len(suggestions)
        content_height = max(3, height - len(head) - input_height)
        wrapped_history = wrap_history_lines(self.history, width)

        if self.scroll_offset:
            end = max(content_height, len(wrapped_history) - self.scroll_offset)
            start = max(0, end - content_height)
        else:
            end = len(wrapped_history)
            start = max(0, end - content_height)

        visible = wrapped_history[start:end]
        visible = visible + [""] * max(0, content_height - len(visible))

        self.last_width = width
        self.last_height = height
        self.input_row = height - (len(suggestions) + 2)
        self.footer_row = height

        sys.stdout.write("\033[?25l\033[H\033[2J")
        for line in head:
            sys.stdout.write(line[: width + 40] + "\n")
        for line in visible:
            sys.stdout.write(line[:width].ljust(width) + "\n")

        sys.stdout.write(color("-" * width, C.gray) + "\n")
        sys.stdout.write(self.input_line(width) + "\n")
        for line in suggestions:
            sys.stdout.write(line[:width].ljust(width) + "\n")
        sys.stdout.write(color("-" * width, C.gray) + "\n")
        footer = status or "? for shortcuts   Ctrl+V paste image/text   PgUp/PgDn scroll   Esc clear input   Ctrl+C exit"
        sys.stdout.write(("  " + footer)[:width].ljust(width))
        sys.stdout.flush()

    def input_line(self, width=None):
        width = width or self.last_width or terminal_width()
        placeholder = "Type your message or @path/to/file"
        shown, cursor_display = self.display_text_and_cursor() if self.buffer else (placeholder, len(placeholder))
        input_color = C.white if self.buffer else C.gray
        caret = color("_", C.bg_input + C.white)
        max_input = max(10, width - 5)
        if len(shown) > max_input:
            start = min(max(0, cursor_display - max_input + 1), len(shown) - max_input)
            shown = shown[start:start + max_input]
            cursor_display = max(0, min(cursor_display - start, len(shown)))
        visible = shown[:cursor_display] + caret + shown[cursor_display:]
        input_pad = " " * max(0, width - len(shown) - 4)
        return C.bg_input + C.bold + C.magenta + "> " + C.bg_input + input_color + visible + input_pad + C.reset

    def render_input_only(self):
        if not self.input_row:
            self.render()
            return
        width = self.last_width or terminal_width()
        sys.stdout.write(f"\033[{self.input_row};1H")
        sys.stdout.write(self.input_line(width))
        sys.stdout.write(f"\033[{self.footer_row};1H")
        sys.stdout.flush()

    def read_key(self):
        import msvcrt

        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            code = msvcrt.getwch()
            return ("special", code)
        return ("char", ch)

    def capture_command(self, text):
        lowered = text.lower().strip()
        if lowered == "/sessions":
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                print_sessions()
            self.append(output.getvalue().strip())
            return False
        if lowered == "/new":
            self.new_session()
            return False
        if lowered == "/resume" or lowered.startswith("/resume "):
            arg = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else ""
            if not arg:
                self.append("Usage: /resume <session-id>")
                return False
            self.resume_session(arg)
            return False
        if lowered == "/model" or lowered.startswith("/model "):
            arg = text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else ""
            self.handle_model_command(arg)
            return False
        if lowered == "/clear":
            self.history = []
            self.scroll_offset = 0
            return False
        if lowered.startswith("/run "):
            command = text.split(maxsplit=1)[1].strip()
            if command and not any(command.lower().startswith(prefix) for prefix in SAFE_RUN_PREFIXES):
                self.append("Unsafe command was not run from the pinned terminal UI.")
                self.append("Use a normal Command Prompt for manual high-risk commands, or run a safe project command.")
                return False
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            next_cwd, should_exit = handle_command(text, self.cwd, self.messages)
        self.cwd = next_cwd
        captured = output.getvalue().strip()
        if captured:
            self.append(captured)
        return should_exit

    def submit(self):
        display_text = self.display_buffer().strip()
        text = self.prompt_buffer()
        self.clear_input()
        if not text:
            return

        self.append(f"> {display_text}")
        if text in {"?", "/?"}:
            text = "/help"
        if text.startswith("/"):
            should_exit = self.capture_command(text)
            if should_exit:
                self.running = False
            return

        if self.agent is None:
            self.append("Loading GCode agent...")
            self.render("Loading agent...")
            self.agent = get_agent()

        self.messages.append({"role": "user", "content": text})
        self.save_current_session()
        self.append("Thinking...")
        self.render("Thinking...")
        try:
            result = self.agent.chat(
                self.messages,
                system_prompt=self.terminal_system_prompt(),
                model_override=self.terminal_model_override(),
            )
            if isinstance(result, tuple):
                answer, tool_results = result
            else:
                answer, tool_results = str(result), []
        except Exception as exc:
            self.append(f"Agent error: {exc}")
            return

        self.messages.append({"role": "assistant", "content": answer})
        self.append_block("GCode", answer)
        if tool_results:
            self.append(f"{len(tool_results)} tool result(s) received.")
        self.save_current_session()

    def run(self):
        enable_ansi()
        if os.name == "nt":
            os.system(f"title GCode Terminal")
        sys.stdout.write("\033[?1049h\033[?25l")
        sys.stdout.flush()
        self.render()
        try:
            while self.running:
                kind, value = self.read_key()
                if kind == "special":
                    if value == "I":  # Page Up
                        self.scroll_offset += 8
                    elif value == "Q":  # Page Down
                        self.scroll_offset = max(0, self.scroll_offset - 8)
                    elif value == "H":  # Up
                        self.scroll_offset += 1
                    elif value == "P":  # Down
                        self.scroll_offset = max(0, self.scroll_offset - 1)
                    elif value == "K":  # Left
                        self.move_cursor_left()
                        self.refresh_input()
                        continue
                    elif value == "M":  # Right
                        self.move_cursor_right()
                        self.refresh_input()
                        continue
                    elif value == "S":  # Delete
                        self.delete_at_cursor()
                        self.refresh_input()
                        continue
                    self.render()
                    continue

                ch = value
                if ch == "\x03":
                    break
                if ch == "\x16":
                    status = self.paste_from_clipboard()
                    self.render(status)
                    continue
                if ch == "\r":
                    self.submit()
                    self.render()
                    continue
                if ch in ("\b", "\x7f"):
                    self.delete_before_cursor()
                    self.refresh_input()
                    continue
                if ch == "\x1b":
                    self.clear_input()
                    self.render()
                    continue
                if ch == "\t":
                    if self.accept_first_suggestion():
                        self.render()
                    continue
                if ch >= " ":
                    self.insert_text(ch)
                    self.refresh_input()
        finally:
            saved = self.save_current_session()
            session_id = self.current_session_id
            sys.stdout.write("\033[?25h\033[0m\033[?1049l")
            if saved:
                sys.stdout.write(f"\nGCode Terminal session saved: {session_id}\n")
                sys.stdout.write(f"Resume with: gcode terminal --resume {session_id}\n")
            else:
                sys.stdout.write("\nGCode Terminal closed. No chat messages to save.\n")
            sys.stdout.flush()


def resolve_inside(base, path_text="."):
    candidate = (base / path_text).resolve()
    root = ROOT.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("Path is outside the GCode project folder.")
    return candidate


def print_file_preview(path):
    if not path.exists():
        print(color("File not found.", C.red))
        return
    if path.is_dir():
        print(color("That path is a directory.", C.yellow))
        return
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        print(color(f"Could not read file: {exc}", C.red))
        return
    lines = content.splitlines()
    print(color(f"{path.relative_to(ROOT)} ({len(lines)} lines)", C.bold + C.cyan))
    for number, line in enumerate(lines[:120], 1):
        print(color(f"{number:>4} | ", C.gray) + line[:220])
    if len(lines) > 120:
        print(color(f"... {len(lines) - 120} more line(s)", C.gray))


def list_path(path):
    if not path.exists():
        print(color("Path not found.", C.red))
        return
    if path.is_file():
        print(path.relative_to(ROOT))
        return
    entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    for entry in entries:
        marker = "/" if entry.is_dir() else ""
        style = C.cyan if entry.is_dir() else C.reset
        print(color(f"  {entry.name}{marker}", style))


def run_command(command, cwd):
    stripped = command.strip()
    if not stripped:
        print(color("Usage: /run <command>", C.yellow))
        return

    is_safe = any(stripped.lower().startswith(prefix) for prefix in SAFE_RUN_PREFIXES)
    if not is_safe:
        answer = input(color("This command is not in the safe list. Run anyway? [y/N] ", C.yellow)).strip().lower()
        if answer not in {"y", "yes"}:
            print(color("Command cancelled.", C.gray))
            return

    print(color(f"$ {stripped}", C.bold + C.blue))
    try:
        completed = subprocess.run(
            stripped,
            cwd=str(cwd),
            shell=True,
            text=True,
            capture_output=True,
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        print(color("Command timed out after 90 seconds.", C.red))
        return
    except Exception as exc:
        print(color(f"Command failed to start: {exc}", C.red))
        return

    if completed.stdout:
        print(completed.stdout.rstrip())
    if completed.stderr:
        print(color(completed.stderr.rstrip(), C.yellow))
    status_color = C.green if completed.returncode == 0 else C.red
    print(color(f"exit code: {completed.returncode}", status_color))


def popen_python_file(filename):
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    executable = str(python if python.exists() else Path(sys.executable))
    kwargs = {"cwd": str(ROOT)}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    subprocess.Popen([executable, str(ROOT / filename)], **kwargs)


def get_agent():
    print(color("Loading GCode agent...", C.gray))
    from agent import GaziAgent

    return GaziAgent()


TERMINAL_SYSTEM_PROMPT = """
You are GCode Terminal, the terminal interface for GCode AI IDE.
Answer in English by default unless the user explicitly asks for another language.
Keep terminal responses compact, actionable, and readable.
For code generation, prefer tool-based file edits over dumping huge code blocks.
If a task affects local files, explain what changed and where.
When the user message contains [pasted image: path], treat that path as an attached image reference.
"""


def print_assistant(text):
    print("")
    print(color("GCode", C.bold + C.magenta))
    wrapped = textwrap.indent((text or "").strip() or "(empty response)", "  ")
    print(wrapped)
    print("")


def handle_command(raw, cwd, messages):
    parts = raw.split(maxsplit=1)
    command = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if command in {"/exit", "/quit", "/q"}:
        return cwd, True
    if command == "/help":
        print_help()
    elif command == "/about":
        print_upgrade()
    elif command == "/auth":
        print_auth()
    elif command == "/upgrade":
        print_upgrade()
    elif command == "/permissions":
        print_permissions()
    elif command == "/model":
        print_model_config("core")
    elif command == "/models":
        print_models()
    elif command == "/stats":
        topic = arg.strip().lower()
        if topic == "model":
            print_models()
        elif topic == "tools":
            print(color("Tools", C.bold + C.cyan))
            for path in sorted((ROOT / "tools").glob("*.py")):
                if path.name != "__init__.py":
                    print(f"  {path.stem}")
            print("")
        else:
            print_status(cwd, messages)
    elif command == "/status":
        print_status(cwd, messages)
    elif command == "/clear":
        print_banner()
    elif command == "/compress":
        before = len(messages)
        if before <= 8:
            print(color("Context is already compact.", C.green))
        else:
            del messages[:-8]
            print(color(f"Compressed terminal context: kept 8 of {before} turns.", C.green))
    elif command == "/sessions":
        print_sessions()
    elif command == "/resume":
        session_id = arg.strip()
        if not session_id:
            print(color("Usage: /resume <session-id>", C.yellow))
        else:
            _kind, session = get_saved_session(session_id)
            if not session:
                print(color(f"Session not found: {session_id}", C.red))
            else:
                messages[:] = clean_messages(session.get("messages", []))
                print(color(f"Resumed {session_id}. Loaded {len(messages)} message(s).", C.green))
    elif command == "/new":
        messages[:] = []
        print(color("Started a new terminal chat session.", C.green))
    elif command == "/directory":
        print_directory(cwd)
    elif command == "/footer":
        print_footer_info()
    elif command == "/tasks":
        print_tasks_info()
    elif command == "/setup":
        choose_default_interface(force=True)
    elif command == "/mode":
        print(f"Default interface: {get_default_interface(load_config()) or 'not configured'}")
    elif command == "/setmode":
        mode = arg.strip().lower()
        if mode not in {"terminal", "desktop"}:
            print(color("Usage: /setmode terminal|desktop", C.yellow))
        else:
            set_default_interface(mode)
            print(color(f"Default interface set to {mode}.", C.green))
    elif command == "/desktop":
        popen_python_file("main.py")
        print(color("GCode Desktop is starting in a new window.", C.green))
    elif command == "/web":
        popen_python_file("app.py")
        print(color("GCode web server is starting in a new terminal window.", C.green))
    elif command == "/pwd":
        print(cwd)
    elif command == "/cd":
        try:
            target = resolve_inside(cwd, arg or ".")
            if not target.is_dir():
                print(color("That path is not a directory.", C.yellow))
            else:
                cwd = target
                print(color(str(cwd), C.green))
        except Exception as exc:
            print(color(str(exc), C.red))
    elif command == "/ls":
        try:
            list_path(resolve_inside(cwd, arg or "."))
        except Exception as exc:
            print(color(str(exc), C.red))
    elif command == "/read":
        if not arg.strip():
            print(color("Usage: /read <file>", C.yellow))
        else:
            try:
                print_file_preview(resolve_inside(cwd, arg))
            except Exception as exc:
                print(color(str(exc), C.red))
    elif command == "/run":
        run_command(arg, cwd)
    elif command == "/history":
        print(f"Chat turns in memory: {len(messages)}")
    else:
        print(color(f"Unknown command: {command}. Type /help.", C.yellow))
    return cwd, False


def parse_resume_arg(argv):
    argv = list(argv or [])
    for index, value in enumerate(argv):
        lowered = str(value).lower()
        if lowered in {"--resume", "-r", "resume"} and index + 1 < len(argv):
            return argv[index + 1]
        if lowered.startswith("--resume="):
            return value.split("=", 1)[1]
    return None


def main(argv=None):
    resume_session_id = parse_resume_arg(argv)
    if os.name == "nt" and sys.stdin.isatty():
        TerminalTUI(resume_session_id=resume_session_id).run()
        return 0

    print_banner()
    cwd = ROOT
    messages = []
    agent = None
    if resume_session_id:
        _kind, session = get_saved_session(resume_session_id)
        if session:
            messages[:] = clean_messages(session.get("messages", []))
            print(color(f"Resumed {resume_session_id}. Loaded {len(messages)} message(s).", C.green))
        else:
            print(color(f"Session not found: {resume_session_id}", C.red))

    while True:
        try:
            raw = read_terminal_input(cwd)
        except (EOFError, KeyboardInterrupt):
            print("")
            break

        text = raw.strip()
        if not text:
            continue

        if text.startswith("/"):
            cwd, should_exit = handle_command(text, cwd, messages)
            if should_exit:
                break
            continue

        if agent is None:
            agent = get_agent()

        messages.append({"role": "user", "content": text})
        print(color("Thinking...", C.gray))
        try:
            result = agent.chat(messages, system_prompt=TERMINAL_SYSTEM_PROMPT)
            if isinstance(result, tuple):
                answer, tool_results = result
            else:
                answer, tool_results = str(result), []
        except Exception as exc:
            print(color(f"Agent error: {exc}", C.red))
            continue

        messages.append({"role": "assistant", "content": answer})
        print_assistant(answer)
        if tool_results:
            print(color(f"{len(tool_results)} tool result(s) received.", C.gray))

    session_id = resume_session_id or new_session_id()
    saved = save_terminal_session(session_id, messages)
    print(color("Goodbye from GCode Terminal.", C.magenta))
    if saved:
        print(color(f"Resume with: gcode terminal --resume {session_id}", C.green))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

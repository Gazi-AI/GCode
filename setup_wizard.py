import argparse
import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

from gcode_config import (
    CONFIG_PATH,
    ensure_user_path,
    get_default_interface,
    load_config,
    set_default_interface,
)


ROOT = Path(__file__).resolve().parent


COLORS = {
    "bg": "#08090d",
    "panel": "#11131a",
    "panel_2": "#181b24",
    "border": "#2b3040",
    "text": "#f8fafc",
    "muted": "#a3aab8",
    "cyan": "#2dd4ff",
    "purple": "#a78bfa",
    "green": "#34d399",
    "yellow": "#f8ff99",
    "danger": "#fb7185",
}


def _pythonw_or_python():
    venv_pythonw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    venv_python = ROOT / ".venv" / "Scripts" / "python.exe"
    if venv_pythonw.exists():
        return str(venv_pythonw)
    if venv_python.exists():
        return str(venv_python)
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return str(pythonw if pythonw.exists() else Path(sys.executable))


def _launch_after(mode):
    script = "main.py" if mode == "desktop" else "gcode_launcher.py"
    args = [_pythonw_or_python(), str(ROOT / script)]
    if mode == "terminal":
        args.append("terminal")
    kwargs = {"cwd": str(ROOT)}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    subprocess.Popen(args, **kwargs)


class SetupWizard(tk.Tk):
    def __init__(self, install_path=False):
        super().__init__()
        self.install_path = install_path
        self.result = None
        self.card_frames = {}
        self.launch_after_save = tk.BooleanVar(value=False)
        self.mode = tk.StringVar(value=get_default_interface(load_config()) or "")

        self.title("GCode Setup Wizard")
        self.geometry("900x620")
        self.minsize(840, 580)
        self.configure(bg=COLORS["bg"])
        self.option_add("*Font", ("Segoe UI", 10))

        self._build()
        self._center()
        self.protocol("WM_DELETE_WINDOW", self._close_without_save)

    def _center(self):
        self.update_idletasks()
        width = self.winfo_width()
        height = self.winfo_height()
        x = (self.winfo_screenwidth() // 2) - (width // 2)
        y = (self.winfo_screenheight() // 2) - (height // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _build(self):
        shell = tk.Frame(self, bg=COLORS["bg"])
        shell.pack(fill="both", expand=True, padx=22, pady=22)

        sidebar = tk.Frame(shell, bg="#0d1018", width=230, highlightbackground=COLORS["border"], highlightthickness=1)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        logo = tk.Canvas(sidebar, width=86, height=86, bg="#0d1018", highlightthickness=0)
        logo.pack(anchor="w", padx=24, pady=(28, 16))
        for x, y, color in [
            (12, 54, "#38bdf8"), (24, 42, "#22d3ee"), (36, 30, "#a78bfa"),
            (48, 42, "#34d399"), (36, 54, "#facc15"), (60, 30, "#f472b6"),
        ]:
            logo.create_rectangle(x, y, x + 12, y + 12, fill=color, outline="")
        logo.create_line(18, 60, 42, 36, fill="#e0f2fe", width=2)
        logo.create_line(42, 36, 66, 36, fill="#e0f2fe", width=2)

        tk.Label(sidebar, text="GCode", bg="#0d1018", fg=COLORS["text"], font=("Segoe UI", 22, "bold")).pack(anchor="w", padx=24)
        tk.Label(sidebar, text="Local AI IDE setup", bg="#0d1018", fg=COLORS["muted"]).pack(anchor="w", padx=24, pady=(4, 26))

        for number, label, active in [
            ("01", "Install command", True),
            ("02", "Choose interface", True),
            ("03", "Launch GCode", False),
        ]:
            row = tk.Frame(sidebar, bg="#0d1018")
            row.pack(fill="x", padx=24, pady=7)
            fg = COLORS["green"] if active else COLORS["muted"]
            tk.Label(row, text=number, bg="#0d1018", fg=fg, font=("Consolas", 10, "bold")).pack(side="left")
            tk.Label(row, text=label, bg="#0d1018", fg=COLORS["text"] if active else COLORS["muted"]).pack(side="left", padx=(12, 0))

        tk.Label(
            sidebar,
            text="Tip: run gcode setup any time to change this choice.",
            bg="#0d1018",
            fg=COLORS["muted"],
            wraplength=170,
            justify="left",
        ).pack(side="bottom", anchor="w", padx=24, pady=24)

        outer = tk.Frame(shell, bg=COLORS["bg"])
        outer.pack(side="left", fill="both", expand=True, padx=(24, 0))

        tk.Label(
            outer,
            text="Choose your default launch experience",
            bg=COLORS["bg"],
            fg=COLORS["text"],
            font=("Segoe UI", 24, "bold"),
        ).pack(anchor="w", pady=(6, 4))
        tk.Label(
            outer,
            text="This is the screen that decides what opens when you type gcode in Command Prompt.",
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=("Segoe UI", 10),
        ).pack(anchor="w")

        notice = tk.Frame(outer, bg=COLORS["panel"], highlightbackground=COLORS["yellow"], highlightthickness=1)
        notice.pack(fill="x", pady=(22, 18))
        tk.Label(notice, text="Required", bg=COLORS["panel"], fg=COLORS["yellow"], font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=16, pady=(12, 2))
        tk.Label(
            notice,
            text="Pick exactly one default interface. The choice is saved locally and can be changed later without reinstalling.",
            bg=COLORS["panel"],
            fg=COLORS["text"],
            wraplength=560,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(0, 14))

        cards = tk.Frame(outer, bg=COLORS["bg"])
        cards.pack(fill="x", pady=(0, 16))
        self._card(
            cards,
            mode="terminal",
            title="GCode Terminal",
            accent=COLORS["cyan"],
            description="Pinned input bar, scrollable output, slash commands, file preview, safe command running, and AI chat in one terminal.",
            command_preview="gcode terminal",
        ).pack(side="left", fill="both", expand=True, padx=(0, 10))
        self._card(
            cards,
            mode="desktop",
            title="GCode Desktop",
            accent=COLORS["purple"],
            description="The full graphical pywebview app from main.py. Choose this if you want GCode to open as a desktop window.",
            command_preview="gcode desktop",
        ).pack(side="left", fill="both", expand=True, padx=(10, 0))

        details = tk.Frame(outer, bg=COLORS["panel"], highlightbackground=COLORS["border"], highlightthickness=1)
        details.pack(fill="x", pady=(0, 16))
        tk.Label(details, text="Command preview", bg=COLORS["panel"], fg=COLORS["green"], font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(12, 2))
        tk.Label(details, text="After saving, open a new Command Prompt and run:", bg=COLORS["panel"], fg=COLORS["muted"]).pack(anchor="w", padx=16)
        tk.Label(details, text="gcode", bg=COLORS["panel"], fg=COLORS["text"], font=("Consolas", 16, "bold")).pack(anchor="w", padx=16, pady=(6, 12))
        tk.Label(details, text=f"Launcher: {ROOT / 'gcode.cmd'}", bg=COLORS["panel"], fg=COLORS["muted"], anchor="w").pack(fill="x", padx=16)
        tk.Label(details, text=f"Config: {CONFIG_PATH}", bg=COLORS["panel"], fg=COLORS["muted"], anchor="w").pack(fill="x", padx=16, pady=(3, 14))

        tk.Checkbutton(
            outer,
            text="Launch the selected interface after saving",
            variable=self.launch_after_save,
            bg=COLORS["bg"],
            fg=COLORS["text"],
            activebackground=COLORS["bg"],
            activeforeground=COLORS["text"],
            selectcolor=COLORS["panel_2"],
        ).pack(anchor="w", pady=(0, 18))

        footer = tk.Frame(outer, bg=COLORS["bg"])
        footer.pack(fill="x", side="bottom")
        self.status = tk.Label(footer, text="", bg=COLORS["bg"], fg=COLORS["danger"], anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        tk.Button(
            footer,
            text="Cancel",
            command=self._close_without_save,
            bg=COLORS["panel_2"],
            fg=COLORS["text"],
            activebackground=COLORS["border"],
            activeforeground=COLORS["text"],
            relief="flat",
            padx=18,
            pady=8,
        ).pack(side="right", padx=(8, 0))
        tk.Button(
            footer,
            text="Save Setup",
            command=self._save,
            bg=COLORS["green"],
            fg="#04110c",
            activebackground="#6ee7b7",
            activeforeground="#04110c",
            relief="flat",
            padx=20,
            pady=8,
            font=("Segoe UI", 10, "bold"),
        ).pack(side="right")
        self._update_card_styles()

    def _card(self, parent, mode, title, accent, description, command_preview):
        frame = tk.Frame(parent, bg=COLORS["panel_2"], highlightbackground=COLORS["border"], highlightthickness=1)
        self.card_frames[mode] = (frame, accent)
        top = tk.Frame(frame, bg=COLORS["panel_2"])
        top.pack(fill="x", padx=16, pady=(16, 8))
        tk.Radiobutton(
            top,
            variable=self.mode,
            value=mode,
            bg=COLORS["panel_2"],
            activebackground=COLORS["panel_2"],
            selectcolor=COLORS["bg"],
            fg=accent,
            activeforeground=accent,
            command=lambda: self._mark_selected(mode),
        ).pack(side="left", padx=(0, 8))
        tk.Label(top, text=title, bg=COLORS["panel_2"], fg=accent, font=("Segoe UI", 14, "bold")).pack(side="left")
        tk.Label(
            frame,
            text=description,
            bg=COLORS["panel_2"],
            fg=COLORS["text"],
            wraplength=285,
            justify="left",
        ).pack(anchor="w", padx=18, pady=(0, 16))
        tk.Label(
            frame,
            text=command_preview,
            bg="#0b0d12",
            fg=accent,
            font=("Consolas", 11, "bold"),
            padx=10,
            pady=6,
        ).pack(anchor="w", padx=18, pady=(0, 18))
        frame.bind("<Button-1>", lambda _event: self._select(mode))
        for child in frame.winfo_children():
            child.bind("<Button-1>", lambda _event, selected=mode: self._select(selected))
        return frame

    def _select(self, mode):
        self.mode.set(mode)
        self._mark_selected(mode)

    def _mark_selected(self, _mode):
        self.status.config(text="")
        self._update_card_styles()

    def _update_card_styles(self):
        selected = self.mode.get().strip().lower()
        for mode, (frame, accent) in self.card_frames.items():
            frame.config(highlightbackground=accent if mode == selected else COLORS["border"], highlightthickness=2 if mode == selected else 1)

    def _save(self):
        selected = self.mode.get().strip().lower()
        if selected not in {"terminal", "desktop"}:
            self.status.config(text="Choose Terminal or Desktop before saving.")
            return
        try:
            set_default_interface(selected)
            if self.install_path:
                ensure_user_path(ROOT)
        except Exception as exc:
            messagebox.showerror("GCode Setup", f"Setup could not be saved:\n{exc}")
            return
        self.result = selected
        messagebox.showinfo(
            "GCode Setup",
            f"Saved. The gcode command will open GCode {selected.title()}.\n\nOpen a new Command Prompt if gcode was just installed.",
        )
        if self.launch_after_save.get():
            _launch_after(selected)
        self.destroy()

    def _close_without_save(self):
        if not get_default_interface(load_config()):
            self.status.config(text="A default interface is required before closing.")
            return
        self.destroy()


def run_setup_wizard(force=False, install_path=False):
    current = get_default_interface(load_config())
    if current and not force and not install_path:
        return current
    app = SetupWizard(install_path=install_path)
    app.mainloop()
    return app.result or get_default_interface(load_config())


def main(argv=None):
    parser = argparse.ArgumentParser(description="GCode graphical setup wizard")
    parser.add_argument("--install", action="store_true", help="Also add the project folder to the user PATH.")
    parser.add_argument("--force", action="store_true", help="Show the wizard even if a default already exists.")
    args = parser.parse_args(argv)
    selected = run_setup_wizard(force=args.force or args.install, install_path=args.install)
    return 0 if selected else 1


if __name__ == "__main__":
    raise SystemExit(main())

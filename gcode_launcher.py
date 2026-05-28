import os
import subprocess
import sys
from pathlib import Path

from gcode_config import choose_default_interface, print_config_status, set_default_interface


ROOT = Path(__file__).resolve().parent


def _python():
    venv_python = ROOT / ".venv" / "Scripts" / "python.exe"
    return str(venv_python if venv_python.exists() else Path(sys.executable))


def _run_python_file(filename):
    target = ROOT / filename
    return subprocess.call([_python(), str(target)], cwd=str(ROOT))


def launch_desktop():
    return _run_python_file("main.py")


def launch_web():
    return _run_python_file("app.py")


def launch_terminal():
    os.chdir(ROOT)
    from terminal_ui import main

    return main()


def print_help():
    print("GCode launcher")
    print("=" * 50)
    print("Usage:")
    print("  gcode                 Open your configured default interface")
    print("  gcode setup           Run the setup wizard again")
    print("  gcode terminal        Open GCode Terminal once")
    print("  gcode desktop         Open GCode Desktop once")
    print("  gcode web             Start the browser web server")
    print("  gcode set terminal    Make Terminal the default")
    print("  gcode set desktop     Make Desktop the default")
    print("  gcode status          Show saved configuration")
    print("  gcode help            Show this help")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    command = (argv[0].lower() if argv else "").strip()

    if command in {"help", "-h", "--help", "/?"}:
        print_help()
        return 0

    if command in {"setup", "config", "configure"}:
        choose_default_interface(force=True)
        return 0

    if command == "status":
        print_config_status()
        return 0

    if command == "set":
        if len(argv) < 2 or argv[1].lower() not in {"terminal", "desktop"}:
            print("Usage: gcode set terminal|desktop")
            return 2
        set_default_interface(argv[1].lower())
        print(f"Default interface set to {argv[1].lower()}.")
        return 0

    if command == "terminal":
        return launch_terminal() or 0

    if command == "desktop":
        return launch_desktop()

    if command == "web":
        return launch_web()

    if command:
        print(f"Unknown command: {command}")
        print("Run 'gcode help' for available commands.")
        return 2

    mode = choose_default_interface(force=False)
    if mode == "desktop":
        return launch_desktop()
    return launch_terminal() or 0


if __name__ == "__main__":
    raise SystemExit(main())

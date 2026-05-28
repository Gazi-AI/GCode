import json
import os
from pathlib import Path


APP_NAME = "GCode"
VALID_DEFAULT_INTERFACES = {"terminal", "desktop"}
CONFIG_DIR = Path(os.environ.get("APPDATA") or Path.home() / ".config") / APP_NAME
CONFIG_PATH = CONFIG_DIR / "config.json"


def load_config():
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_config(config):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")


def get_default_interface(config=None):
    config = config if config is not None else load_config()
    value = str(config.get("default_interface", "")).strip().lower()
    return value if value in VALID_DEFAULT_INTERFACES else None


def set_default_interface(mode):
    mode = str(mode or "").strip().lower()
    if mode not in VALID_DEFAULT_INTERFACES:
        raise ValueError("Default interface must be 'terminal' or 'desktop'.")
    config = load_config()
    config["default_interface"] = mode
    save_config(config)
    return config


def choose_default_interface_cli(force=False):
    config = load_config()
    current = get_default_interface(config)
    if current and not force:
        return current

    print("")
    print("GCode Setup Wizard")
    print("=" * 50)
    print("Choose what should open when you type 'gcode'.")
    print("")
    print("  1) GCode Terminal  - keyboard-first terminal interface")
    print("  2) GCode Desktop   - pywebview desktop window from main.py")
    print("")
    print("This choice is required. You can change it later with:")
    print("  gcode setup")
    print("")

    while True:
        answer = input("Default interface [1=Terminal, 2=Desktop]: ").strip().lower()
        if answer in {"1", "terminal", "term", "t"}:
            set_default_interface("terminal")
            print("")
            print("Saved: 'gcode' will open GCode Terminal.")
            return "terminal"
        if answer in {"2", "desktop", "desk", "d"}:
            set_default_interface("desktop")
            print("")
            print("Saved: 'gcode' will open GCode Desktop.")
            return "desktop"
        print("Please choose 1 or 2.")


def choose_default_interface(force=False, use_gui=True):
    config = load_config()
    current = get_default_interface(config)
    if current and not force:
        return current

    if use_gui:
        try:
            from setup_wizard import run_setup_wizard

            selected = run_setup_wizard(force=force)
            if selected:
                return selected
        except Exception as exc:
            print(f"GUI setup could not be opened; falling back to terminal setup. {exc}")

    return choose_default_interface_cli(force=True)


def ensure_user_path(project_root):
    project_root = str(Path(project_root).resolve())
    current_user_path = os.environ.get("PATH", "")
    stored_user_path = os.environ.get("GCODE_TEST_USER_PATH")
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as key:
                stored_user_path = winreg.QueryValueEx(key, "Path")[0]
        except Exception:
            stored_user_path = stored_user_path or ""
    else:
        stored_user_path = stored_user_path or current_user_path

    path_parts = [part for part in str(stored_user_path or "").split(os.pathsep) if part.strip()]
    normalized_project_root = project_root.rstrip("\\/")
    for part in path_parts:
        try:
            normalized_part = str(Path(part).resolve()).rstrip("\\/")
        except Exception:
            normalized_part = str(part).rstrip("\\/")
        if normalized_part.lower() == normalized_project_root.lower():
            return False

    updated = os.pathsep.join(path_parts + [project_root])
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, updated)
            try:
                import ctypes

                HWND_BROADCAST = 0xFFFF
                WM_SETTINGCHANGE = 0x001A
                SMTO_ABORTIFHUNG = 0x0002
                ctypes.windll.user32.SendMessageTimeoutW(
                    HWND_BROADCAST,
                    WM_SETTINGCHANGE,
                    0,
                    "Environment",
                    SMTO_ABORTIFHUNG,
                    5000,
                    None,
                )
            except Exception:
                pass
            return True
        except Exception:
            pass

    os.environ["GCODE_TEST_USER_PATH"] = updated
    return True


def print_config_status():
    config = load_config()
    mode = get_default_interface(config)
    print("GCode configuration")
    print("=" * 50)
    print(f"Config file: {CONFIG_PATH}")
    print(f"Default interface: {mode or 'not configured'}")

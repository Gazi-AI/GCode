import subprocess
import os
import re

TOOL_DEFINITION = {
    "name": "shell_executor",
    "description": "Runs terminal/shell commands inside the workspace, for example npm install, python script.py, or git status.",
    "emoji": "TERM",
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Terminal command to run"
            },
            "timeout": {
                "type": "integer",
                "description": "Optional timeout in seconds (1-300)"
            },
            "background": {
                "type": "boolean",
                "description": "When true, starts the command in the background and returns its pid immediately"
            },
            "cwd": {
                "type": "string",
                "description": "Optional working directory. Must be a relative path inside the workspace, for example 'agentic-taskboard'."
            }
        },
        "required": ["command"]
    }
}

def _normalize_project_path(path):
    raw = str(path or "").strip().replace("\\", "/").strip("\"'")
    raw = raw.replace("%USERPROFILE%", "").replace("$HOME", "")
    raw = re.sub(r"^[a-zA-Z]:/+", "", raw)
    raw = raw.lstrip("/")
    parts = [p for p in raw.split("/") if p not in ("", ".", "~")]
    lowered = [p.lower() for p in parts]

    desktop_names = {"desktop", "masaustu", "masaÃ¼stÃ¼"}
    if any(p in desktop_names for p in lowered):
        last_desktop = max(i for i, p in enumerate(lowered) if p in desktop_names)
        parts = parts[last_desktop + 1:]
        lowered = [p.lower() for p in parts]

    if lowered and lowered[0] == "gcode":
        parts = parts[1:]

    clean_parts = []
    for part in parts:
        if part == "..":
            raise ValueError("CWD traversal outside the project was blocked")
        safe = re.sub(r'[<>:"|?*]', "_", part).strip()
        if safe:
            clean_parts.append(safe)
    return "/".join(clean_parts)

def execute(params):
    command = params.get("command")
    if not command or not isinstance(command, str):
        return {"error": "Command is required"}

    timeout = params.get("timeout", 120)
    try:
        timeout = max(1, min(int(timeout), 300))
    except (TypeError, ValueError):
        timeout = 120
    background = bool(params.get("background", False))
        
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cwd = base_dir
    requested_cwd = params.get("cwd")
    if requested_cwd:
        try:
            rel_cwd = _normalize_project_path(requested_cwd)
            cwd = os.path.abspath(os.path.join(base_dir, rel_cwd))
            if os.path.commonpath([base_dir, cwd]) != base_dir:
                return {"error": "CWD outside the workspace was blocked"}
            if not os.path.isdir(cwd):
                return {"error": f"Working directory not found: {rel_cwd}"}
        except Exception as exc:
            return {"error": str(exc)}
    
    try:
        if background:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True
            )
            return {
                "success": True,
                "pid": process.pid,
                "stdout": "",
                "stderr": "",
                "returncode": None,
                "message": f"Command started in the background (pid: {process.pid})"
            }

        result = subprocess.run(
            command,
            cwd=cwd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode
        }
    except subprocess.TimeoutExpired as e:
        return {
            "error": f"Command did not finish within {timeout} seconds",
            "stdout": e.stdout or "",
            "stderr": e.stderr or "",
            "returncode": None
        }
    except Exception as e:
        return {"error": str(e)}

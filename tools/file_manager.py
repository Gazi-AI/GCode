import os
import re

TOOL_DEFINITION = {
    "name": "file_manager",
    "description": "Performs file system operations: create, read, write, delete, and list files inside the GCode workspace.",
    "emoji": "FILE",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "'read', 'write', 'delete', or 'list'"
            },
            "path": {
                "type": "string",
                "description": "File or directory path, for example 'main.py' or 'src/app.js'"
            },
            "content": {
                "type": "string",
                "description": "File content for write operations"
            }
        },
        "required": ["action", "path"]
    }
}

def _normalize_project_path(path):
    raw = str(path or "").strip().replace("\\", "/").strip("\"'")
    raw = raw.replace("%USERPROFILE%", "").replace("$HOME", "")
    raw = re.sub(r"^[a-zA-Z]:/+", "", raw)
    raw = raw.lstrip("/")
    parts = [p for p in raw.split("/") if p not in ("", ".", "~")]
    lowered = [p.lower() for p in parts]

    desktop_names = {"desktop", "masaÃ¼stÃ¼", "masaustu"}
    if any(p in desktop_names for p in lowered):
        last_desktop = max(i for i, p in enumerate(lowered) if p in desktop_names)
        parts = parts[last_desktop + 1:]
        lowered = [p.lower() for p in parts]

    if lowered and lowered[0] == "gcode":
        parts = parts[1:]

    clean_parts = []
    for part in parts:
        if part == "..":
            raise ValueError("Path traversal outside the project was blocked")
        safe = re.sub(r'[<>:"|?*]', "_", part).strip()
        if safe:
            clean_parts.append(safe)

    if not clean_parts:
        raise ValueError("A valid path is required")
    return "/".join(clean_parts)

def execute(params):
    action = params.get("action")
    path = params.get("path")
    content = params.get("content", "")
    
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not path or not isinstance(path, str):
        return {"error": "A valid path is required"}

    try:
        rel_path = _normalize_project_path(path)
    except ValueError as exc:
        return {"error": str(exc)}

    target_path = os.path.abspath(os.path.join(base_dir, rel_path))
    if os.path.commonpath([base_dir, target_path]) != base_dir:
        return {"error": "Access outside the project folder was blocked"}
    
    if action == "list":
        if os.path.isdir(target_path):
            return {"files": sorted(os.listdir(target_path))}
        return {"error": "Directory not found"}
    
    elif action == "read":
        if os.path.isfile(target_path):
            with open(target_path, "r", encoding="utf-8") as f:
                return {"content": f.read()}
        return {"error": "File not found"}
        
    elif action == "write":
        parent_dir = os.path.dirname(target_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"success": True, "message": f"{rel_path} was created or updated."}
        
    elif action == "delete":
        if os.path.isfile(target_path):
            os.remove(target_path)
            return {"success": True, "message": f"{rel_path} was deleted."}
        return {"error": "File not found"}
        
    return {"error": "Invalid action"}

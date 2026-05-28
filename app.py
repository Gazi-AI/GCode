"""
GCode main Flask server.

Chat history is stored by the server in vault_data/chats.json, so the web UI
does not depend on browser localStorage for conversations.
"""

import os
import sys
import json
import time
import webbrowser
import threading
import asyncio
import edge_tts
import difflib
import uuid
import py_compile
import re
import html
import unicodedata
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agent import GaziAgent, LOGO

app = Flask(__name__, static_folder="static")
CORS(app)

# Agent baÃ…Å¸lat
print("\n>> GaziGPT Baslatiliyor...")
agent = GaziAgent()
print(f">> {len(agent.tool_manager.tools)} arac yuklendi!\n")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "vault_data")
CHAT_STORE_PATH = os.path.join(DATA_DIR, "chats.json")
EDIT_LOCK = threading.RLock()
CHAT_LOCK = threading.RLock()
EDIT_STATE = {
    "pending": {},
    "undo": [],
    "redo": [],
}
SAFE_COMMAND_PREFIXES = (
    "python app.py",
    "python main.py",
    "python -m compileall",
    "python -m py_compile",
    "flask run",
    "node --check",
    "npm run dev",
    "npm run build",
    "npm run start",
    "npm run preview",
    "npm test",
    "pytest",
)


def _empty_chat_store():
    return {
        "version": 1,
        "current_chat_id": None,
        "chats": {},
    }


def _load_chat_store():
    with CHAT_LOCK:
        if not os.path.exists(CHAT_STORE_PATH):
            return _empty_chat_store()
        try:
            with open(CHAT_STORE_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError):
            return _empty_chat_store()

        if not isinstance(raw, dict):
            return _empty_chat_store()

        chats = raw.get("chats", {})
        if not isinstance(chats, dict):
            chats = {}

        current_chat_id = raw.get("current_chat_id")
        if current_chat_id not in chats:
            current_chat_id = None

        return {
            "version": 1,
            "current_chat_id": current_chat_id,
            "chats": chats,
        }


def _save_chat_store(store):
    with CHAT_LOCK:
        chats = store.get("chats", {}) if isinstance(store, dict) else {}
        if not isinstance(chats, dict):
            raise ValueError("chats must be an object")

        current_chat_id = store.get("current_chat_id") if isinstance(store, dict) else None
        if current_chat_id not in chats:
            current_chat_id = None

        clean_store = {
            "version": 1,
            "current_chat_id": current_chat_id,
            "chats": chats,
        }
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp_path = CHAT_STORE_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(clean_store, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, CHAT_STORE_PATH)
        return clean_store


def _normalize_project_path(path):
    raw = str(path or "").strip().replace("\\", "/").strip("`\"'Ã¢â‚¬Å“Ã¢â‚¬ÂÃ¢â‚¬ËœÃ¢â‚¬â„¢")
    raw = raw.replace("%USERPROFILE%", "").replace("$HOME", "")
    raw = re.sub(r"^[a-zA-Z]:/+", "", raw)
    raw = raw.lstrip("/")
    parts = [p for p in raw.split("/") if p not in ("", ".", "~")]
    lowered = [p.lower() for p in parts]

    desktop_names = {"desktop", "masaÃƒÂ¼stÃƒÂ¼", "masaustu"}
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
        raise ValueError("A valid file path is required")
    return "/".join(clean_parts)


def _safe_project_path(path):
    if not path or not isinstance(path, str):
        raise ValueError("A valid file path is required")
    rel_path = _normalize_project_path(path)
    target = os.path.abspath(os.path.join(BASE_DIR, rel_path))
    if os.path.commonpath([BASE_DIR, target]) != BASE_DIR:
        raise ValueError("Access outside the project folder was blocked")
    rel = os.path.relpath(target, BASE_DIR).replace("\\", "/")
    return target, rel


def _read_file_snapshot(path):
    target, rel = _safe_project_path(path)
    exists = os.path.exists(target)
    if exists and os.path.isfile(target):
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            return {"path": rel, "exists": True, "content": f.read()}
    return {"path": rel, "exists": False, "content": ""}


def _diff_stats(old_text, new_text):
    old_lines = (old_text or "").splitlines()
    new_lines = (new_text or "").splitlines()
    added = 0
    removed = 0
    for line in difflib.ndiff(old_lines, new_lines):
        if line.startswith("+ "):
            added += 1
        elif line.startswith("- "):
            removed += 1
    return added, removed


def _unique_rel_path(rel_path):
    rel_path = str(rel_path or "").replace("\\", "/").strip("/")
    directory, filename = os.path.split(rel_path)
    stem, ext = os.path.splitext(filename)
    if not stem:
        stem = "dosya"
    counter = 1
    while True:
        candidate_name = f"{stem}{counter}{ext}"
        candidate_rel = f"{directory}/{candidate_name}" if directory else candidate_name
        candidate_abs = os.path.abspath(os.path.join(BASE_DIR, candidate_rel))
        if os.path.commonpath([BASE_DIR, candidate_abs]) != BASE_DIR:
            raise ValueError("Access outside the project folder was blocked")
        if not os.path.exists(candidate_abs):
            return candidate_rel.replace("\\", "/")
        counter += 1


def _unique_root_name(root):
    root = str(root or "").strip("/\\")
    stem, ext = os.path.splitext(root)
    if not stem:
        stem = "proje"
    counter = 1
    while True:
        candidate = f"{stem}{counter}{ext}"
        candidate_abs = os.path.abspath(os.path.join(BASE_DIR, candidate))
        if os.path.commonpath([BASE_DIR, candidate_abs]) != BASE_DIR:
            raise ValueError("Access outside the project folder was blocked")
        if not os.path.exists(candidate_abs):
            return candidate
        counter += 1


def _is_create_request(text):
    lowered = (text or "").lower()
    ascii_lowered = unicodedata.normalize("NFKD", lowered.replace("\u0131", "i")).encode("ascii", "ignore").decode()
    edit_words_first = ("duzenle", "guncelle", "degistir", "duzelt", "fix")
    if any(word in ascii_lowered for word in edit_words_first):
        return False
    if any(word in ascii_lowered for word in ("olusturma", "olusturmay", "ekleme", "sil", "kaldir")):
        return False
    create_words_first = ("olustur", "yarat", "kur", "proje", "klasor", "dosya")
    if any(word in ascii_lowered for word in create_words_first):
        return True
    followup_markers = (
        "hayir", "hayÃ„Â±r", "ona", "onu", "buna", "suna", "Ã…Å¸una", "mevcut",
        "var olan", "ekle", "ekleme", "sil", "kaldir", "kaldÃ„Â±r", "sadece",
    )
    if any(word in lowered for word in followup_markers):
        return False
    edit_words = ("duzenle", "dÃƒÂ¼zenle", "guncelle", "gÃƒÂ¼ncelle", "degistir", "deÃ„Å¸iÃ…Å¸tir", "duzelt", "dÃƒÂ¼zelt", "fix")
    if any(word in lowered for word in edit_words):
        return False
    create_words = ("olustur", "oluÃ…Å¸tur", "yarat", "yap", "kur", "proje", "klasor", "klasÃƒÂ¶r", "dosya")
    return any(word in lowered for word in create_words)


def _explicit_file_paths_from_text(text):
    pattern = r"(?<![\w/.-])(?:[a-zA-Z0-9_\-]+/)*[a-zA-Z0-9_\-]+\.(?:py|html|css|js|json|md|txt)(?![\w/-])"
    return [match.group(0).rstrip(".,;:)").replace("\\", "/").lstrip("/") for match in re.finditer(pattern, text or "", re.IGNORECASE)]


def _looks_like_python_desktop_request(text):
    lowered = (text or "").lower()
    if any(word in lowered for word in ("tkinter", "tk inter", "tkÃ„Â±nter", "tkinter")):
        return True
    if any(path.lower().endswith(".py") for path in _explicit_file_paths_from_text(text)):
        py_repair_words = ("safe_eval", "py_compile", "hesap makinesi", "calculator", "button", "entry")
        if any(word in lowered for word in py_repair_words):
            return True
    desktop_words = ("hesap makinesi", "calculator", "arayuz", "arayÃƒÂ¼z", "gui", "pencere")
    web_words = ("html", "css", "javascript", "react", "vite", "site", "web")
    return "python" in lowered and any(word in lowered for word in desktop_words) and not any(word in lowered for word in web_words)


def _looks_like_python_file_only_request(text):
    lowered = (text or "").lower()
    if not any(path.lower().endswith(".py") for path in _explicit_file_paths_from_text(text)):
        return False
    file_only_markers = (
        "yeni dosya", "olusturma", "oluÃ…Å¸turma", "html", "css", "js", "index.html",
        "sadece", "dosyasini duzelt", "dosyasÃ„Â±nÃ„Â± dÃƒÂ¼zelt", "guncelle", "gÃƒÂ¼ncelle",
        "safe_eval", "py_compile",
    )
    return any(marker in lowered for marker in file_only_markers)


def _latest_edited_path(suffix=None):
    suffix = suffix.lower() if suffix else None
    with EDIT_LOCK:
        batches = list(EDIT_STATE.get("undo", [])) + list(EDIT_STATE.get("pending", {}).values())
    for batch in reversed(batches):
        for change in reversed(batch.get("changes", [])):
            path = str(change.get("path", "")).replace("\\", "/").strip("/")
            if not path:
                continue
            if suffix is None or path.lower().endswith(suffix):
                return path
    return ""


def _latest_python_path_from_history(history_messages):
    for message in reversed(history_messages or []):
        content = str(message.get("content", "") if isinstance(message, dict) else "")
        for path in reversed(_explicit_file_paths_from_text(content)):
            if path.lower().endswith(".py"):
                return path
    return ""


def _desktop_target_path(user_request, history_messages=None):
    explicit_py = [path for path in _explicit_file_paths_from_text(user_request) if path.lower().endswith(".py")]
    if explicit_py:
        return explicit_py[0]
    return _latest_edited_path(".py") or _latest_python_path_from_history(history_messages)


def _contextualize_followup_request(history_messages, user_request):
    target_py = _desktop_target_path(user_request, history_messages)
    if not target_py or not (
        _looks_like_python_desktop_request(user_request)
        or _looks_like_python_file_only_request(user_request)
    ):
        return user_request
    context = (
        "\n\n[Sistem baglami: Bu mesaj onceki kodlama isteginin devamidir. "
        f"Hedef dosya: {target_py}. Bu istekte yeni web dosyasi olusturulmayacak; hedef Python dosyasi guncellenecek. "
        "HTML, CSS, JavaScript, Flask, React, Vite veya index.html dosyasi olusturma. "
        "Kullanici 'ona/onu/buna' dediyse bu hedef dosyayi kasteder.]"
    )
    return user_request + context


def _strip_extended_code_payload(text):
    cleaned = str(text or "")
    cleaned = re.sub(r"```[\s\S]*?```", "", cleaned)
    cleaned = re.sub(r"```[\s\S]*$", "", cleaned)
    cleaned = re.sub(r"(?is)(^|\n)\s*gazi_tool\s*\n[\s\S]*$", r"\1", cleaned)
    cleaned = cleaned.strip()
    if len(cleaned) > 1200:
        cleaned = cleaned[:1200].rstrip() + "..."
    return cleaned


def _retarget_change(change, new_rel):
    after_content = change.get("after", {}).get("content", "")
    before = _read_file_snapshot(new_rel)
    added, removed = _diff_stats(before.get("content", ""), after_content)
    return {
        **change,
        "path": new_rel,
        "before": before,
        "added": added,
        "removed": removed,
        "preview": "\n".join(after_content.splitlines()[:60]),
    }


def _auto_rename_conflicting_changes(changes, user_request=""):
    if not changes or not _is_create_request(user_request):
        return changes

    write_changes = [change for change in changes if change.get("action") == "write"]
    if not write_changes:
        return changes

    paths = [change.get("path", "").replace("\\", "/").strip("/") for change in write_changes]
    roots = [path.split("/", 1)[0] for path in paths if "/" in path]
    if roots and len(roots) == len(paths) and len(set(roots)) == 1:
        root = roots[0]
        root_abs = os.path.abspath(os.path.join(BASE_DIR, root))
        if os.path.exists(root_abs):
            new_root = _unique_root_name(root)
            renamed = []
            for change in changes:
                path = change.get("path", "").replace("\\", "/").strip("/")
                if path == root or path.startswith(f"{root}/"):
                    suffix = path[len(root):].lstrip("/")
                    new_rel = f"{new_root}/{suffix}" if suffix else new_root
                    renamed.append(_retarget_change(change, new_rel))
                else:
                    renamed.append(change)
            return renamed

    renamed = []
    used_paths = set()
    for change in changes:
        path = change.get("path", "").replace("\\", "/").strip("/")
        if change.get("action") == "write":
            target, _ = _safe_project_path(path)
            if os.path.exists(target) or path in used_paths:
                path = _unique_rel_path(path)
                change = _retarget_change(change, path)
        used_paths.add(path)
        renamed.append(change)
    return renamed


def _retarget_changes_to_requested_root(changes, user_request=""):
    if not changes or not _is_create_request(user_request):
        return changes
    try:
        requested_root = agent._requested_project_root(user_request)
    except Exception:
        requested_root = ""
    if not requested_root:
        return changes

    requested_root = _normalize_project_path(requested_root).split("/", 1)[0]
    if not requested_root:
        return changes

    retargeted = []
    structural_roots = {
        "client", "server", "src", "static", "public", "assets", "css", "js",
        "styles", "components", "services", "lib", "utils", "pages", "api",
    }
    throwaway_roots = {
        "gazigpt-js-app",
        "gazigpt_app",
        "gcode-project",
        "vanilla-web-app",
        "app",
        "project",
    }
    for change in changes:
        path = str(change.get("path", "")).replace("\\", "/").strip("/")
        if not path or path == requested_root or path.startswith(f"{requested_root}/"):
            retargeted.append(change)
            continue

        parts = path.split("/")
        first = parts[0].lower() if parts else ""
        if len(parts) > 1 and first in throwaway_roots:
            suffix = "/".join(parts[1:])
        elif len(parts) > 1 and first not in structural_roots:
            suffix = "/".join(parts[1:])
        else:
            suffix = path
        retargeted.append(_retarget_change(change, f"{requested_root}/{suffix}"))
    return retargeted


def _retarget_tool_calls_to_requested_root(calls, user_request=""):
    if not calls or not _is_create_request(user_request):
        return calls
    try:
        requested_root = agent._requested_project_root(user_request)
    except Exception:
        requested_root = ""
    if not requested_root:
        return calls

    requested_root = _normalize_project_path(requested_root).split("/", 1)[0]
    structural_roots = {
        "client", "server", "src", "static", "public", "assets", "css", "js",
        "styles", "components", "services", "lib", "utils", "pages", "api",
    }
    throwaway_roots = {
        "gazigpt-js-app", "gazigpt_app", "gcode-project", "vanilla-web-app",
        "app", "project",
    }

    retargeted = []
    for call in calls:
        if not isinstance(call, dict) or call.get("tool") != "file_manager":
            retargeted.append(call)
            continue
        params = dict(call.get("params", {}))
        action = params.get("action")
        path = str(params.get("path", "")).replace("\\", "/").strip("/")
        if action not in ("write", "delete") or not path or path == requested_root or path.startswith(f"{requested_root}/"):
            retargeted.append({**call, "params": params})
            continue

        parts = path.split("/")
        first = parts[0].lower() if parts else ""
        if len(parts) > 1 and first in throwaway_roots:
            suffix = "/".join(parts[1:])
        elif len(parts) > 1 and first not in structural_roots:
            suffix = "/".join(parts[1:])
        else:
            suffix = path
        params["path"] = f"{requested_root}/{suffix}"
        retargeted.append({**call, "params": params})
    return retargeted


def _build_change_from_tool(params):
    action = params.get("action")
    if action not in ("write", "delete"):
        return None
    _, rel = _safe_project_path(params.get("path"))
    before = _read_file_snapshot(rel)
    after_content = "" if action == "delete" else params.get("content", "")
    added, removed = _diff_stats(before.get("content", ""), after_content)
    preview_lines = after_content.splitlines()[:60]
    return {
        "path": rel,
        "action": action,
        "before": before,
        "after": {
            "exists": action != "delete",
            "content": after_content,
        },
        "added": added,
        "removed": removed,
        "preview": "\n".join(preview_lines),
    }


def _quality_guard_change(change, user_request=""):
    if not change or change.get("action") != "write":
        return change, []

    path = str(change.get("path", "")).replace("\\", "/").strip("/")
    after = change.get("after", {}) if isinstance(change.get("after"), dict) else {}
    content = after.get("content", "")
    if not path or not isinstance(content, str):
        return change, []

    try:
        issues = agent._content_quality_issues(
            content,
            path,
            plan={"folders": []},
            user_question=user_request,
        )
    except Exception as exc:
        return change, [f"{path}: kalite kontrolu calismadi: {exc}"]

    if not issues:
        return change, []

    try:
        candidate = agent._emergency_file_content(
            path,
            {
                "project_name": os.path.splitext(os.path.basename(path))[0] or "gazigpt_app",
                "folders": [],
            },
            user_request,
        )
        candidate_issues = agent._content_quality_issues(
            candidate,
            path,
            plan={"folders": []},
            user_question=user_request,
        )
    except Exception as exc:
        return {**change, "quality_issues": issues}, issues + [f"{path}: auto kalite fallback hatasi: {exc}"]

    if candidate and not candidate_issues and candidate != content:
        fixed = _build_change_from_tool({
            "action": "write",
            "path": path,
            "content": candidate,
        })
        if fixed:
            fixed["quality_fixed"] = True
            fixed["quality_issues"] = issues
            return fixed, issues

    return {**change, "quality_issues": issues}, issues


def _quality_guard_changes(changes, user_request=""):
    guarded = []
    fixes = []
    warnings = []
    for change in changes or []:
        guarded_change, issues = _quality_guard_change(change, user_request=user_request)
        if guarded_change:
            guarded.append(guarded_change)
            if guarded_change.get("quality_fixed"):
                fixes.append({
                    "path": guarded_change.get("path", ""),
                    "issues": issues,
                })
            elif issues:
                warnings.append({
                    "path": guarded_change.get("path", ""),
                    "issues": issues,
                })
    return guarded, fixes, warnings


def _wrong_web_files_for_desktop_cleanup(calls, user_request):
    lowered = (user_request or "").lower()
    if not any(word in lowered for word in ("sil", "ekleme", "kaldir", "kaldÃ„Â±r", "html", "index")):
        return []
    candidates = set()
    for call in calls:
        if call.get("tool") != "file_manager":
            continue
        path = str(call.get("params", {}).get("path", "")).replace("\\", "/").strip("/")
        if path.lower().endswith((".html", ".css", ".js")):
            candidates.add(path)
    for filename in os.listdir(BASE_DIR):
        lowered_name = filename.lower()
        if re.match(r"^index\d*\.html$", lowered_name):
            candidates.add(filename)
    html_dir = os.path.join(BASE_DIR, "HTML")
    if os.path.isdir(html_dir):
        for root, _dirs, filenames in os.walk(html_dir):
            for filename in filenames:
                if filename.lower().endswith((".html", ".css", ".js")):
                    candidates.add(os.path.relpath(os.path.join(root, filename), BASE_DIR).replace("\\", "/"))
    existing = []
    for path in sorted(candidates):
        try:
            target, rel = _safe_project_path(path)
        except ValueError:
            continue
        if os.path.isfile(target):
            existing.append(rel)
    return existing


def _guard_tool_calls_for_request(calls, user_request, internal_request=None):
    effective_request = internal_request or user_request
    should_guard_python_target = (
        _looks_like_python_desktop_request(effective_request)
        or _looks_like_python_file_only_request(effective_request)
    )
    if not should_guard_python_target:
        return calls
    target_py = _desktop_target_path(effective_request)
    guarded = []
    has_good_target_write = False
    removed_web_write = False
    needs_calculator_recovery = _looks_like_python_desktop_request(effective_request)

    for call in calls:
        if not isinstance(call, dict) or call.get("tool") != "file_manager":
            guarded.append(call)
            continue
        params = dict(call.get("params", {}))
        action = params.get("action")
        path = str(params.get("path", "")).replace("\\", "/").strip("/")
        ext = os.path.splitext(path)[1].lower()
        if action == "write" and ext in (".html", ".css", ".js", ".jsx", ".tsx"):
            removed_web_write = True
            continue
        if action == "write" and target_py and ext == ".py":
            params["path"] = target_py
            content = str(params.get("content", ""))
            lowered_content = content.lower()
            if needs_calculator_recovery:
                has_tkinter = "tkinter" in lowered_content or "import tkinter" in lowered_content or "from tkinter" in lowered_content
                has_calculator_logic = "safe_eval" in lowered_content and "button" in lowered_content and "entry" in lowered_content
                buggy_format = "str(round(" in lowered_content and '.rstrip("0")' in content
                if not (has_tkinter and has_calculator_logic) or buggy_format:
                    params["content"] = agent._emergency_file_content(target_py, {"project_name": os.path.splitext(os.path.basename(target_py))[0]}, effective_request)
            has_good_target_write = True
            guarded.append({**call, "params": params})
            continue
        guarded.append(call)

    for rel in _wrong_web_files_for_desktop_cleanup(calls, effective_request):
        guarded.append({"tool": "file_manager", "params": {"action": "delete", "path": rel}})

    if target_py and not has_good_target_write and (removed_web_write or needs_calculator_recovery):
        guarded.append({
            "tool": "file_manager",
            "params": {
                "action": "write",
                "path": target_py,
                "content": agent._emergency_file_content(
                    target_py,
                    {"project_name": os.path.splitext(os.path.basename(target_py))[0]},
                    effective_request,
                ),
            },
        })

    deduped = []
    seen = set()
    for call in guarded:
        if not isinstance(call, dict):
            continue
        params = call.get("params", {}) if isinstance(call.get("params", {}), dict) else {}
        key = (call.get("tool"), params.get("action"), str(params.get("path", "")))
        if key in seen and params.get("action") == "delete":
            continue
        seen.add(key)
        deduped.append(call)
    return deduped


def _plan_payload_from_tool_calls(calls):
    files = []
    for call in calls:
        if call.get("tool") != "file_manager":
            continue
        params = call.get("params", {})
        if params.get("action") not in ("write", "delete"):
            continue
        try:
            path = _normalize_project_path(params.get("path"))
        except ValueError:
            continue
        files.append({
            "path": path,
            "purpose": "File change generated by the model",
            "depends_on": [],
        })
    folders = sorted({os.path.dirname(f["path"]).replace("\\", "/") for f in files if os.path.dirname(f["path"])})
    if not files:
        return None
    return {
        "stage": "tool_plan",
        "plan": {
            "project_name": files[0]["path"].split("/")[0] if "/" in files[0]["path"] else os.path.splitext(files[0]["path"])[0],
            "summary": f"{len(files)} file change(s) detected.",
            "folders": folders,
            "files": files,
            "run_commands": [],
            "test_commands": [],
        },
        "workers": [
            {"path": f["path"], "status": "validated", "purpose": f.get("purpose", "")}
            for f in files
        ],
        "message": f"Plan detected for {len(files)} file(s)",
    }


def _project_folder_for_batch(batch):
    changes = batch.get("changes", []) if isinstance(batch, dict) else []
    paths = [str(change.get("path", "")).replace("\\", "/").strip("/") for change in changes if change.get("path")]
    if not paths:
        rel = ""
    else:
        first_segments = [path.split("/", 1)[0] for path in paths if "/" in path]
        rel = first_segments[0] if first_segments and len(set(first_segments)) == 1 else ""
    target = os.path.abspath(os.path.join(BASE_DIR, rel)) if rel else BASE_DIR
    return {
        "path": rel,
        "absolute_path": target,
        "exists": os.path.isdir(target),
    }


def _preview_stats_for_change(change):
    added = int(change.get("added", 0) or 0)
    removed = int(change.get("removed", 0) or 0)
    before = change.get("before", {}) if isinstance(change.get("before", {}), dict) else {}
    after = change.get("after", {}) if isinstance(change.get("after", {}), dict) else {}
    before_content = before.get("content", "") or ""
    after_content = after.get("content", "") or ""
    if added == 0 and removed == 0 and before_content != after_content:
        added, removed = _diff_stats(before_content, after_content)
    if (
        change.get("action") == "write"
        and added == 0
        and removed == 0
        and after_content
        and not before.get("exists")
    ):
        added = len(after_content.splitlines()) or 1
    return added, removed


def _preview_payload(batch):
    project_folder = _project_folder_for_batch(batch)
    files = []
    for change in batch.get("changes", []):
        added, removed = _preview_stats_for_change(change)
        files.append({
            "path": change["path"],
            "action": change["action"],
            "added": added,
            "removed": removed,
            "preview": change.get("preview", ""),
        })
    return {
        "plan_id": batch["plan_id"],
        "status": batch["status"],
        "files": files,
        "summary": batch.get("summary", ""),
        "project_folder": project_folder,
        "totals": {
            "files": len(files),
            "added": sum(f["added"] for f in files),
            "removed": sum(f["removed"] for f in files),
        },
    }


def _apply_batch(batch):
    applied = []
    for change in batch.get("changes", []):
        target, rel = _safe_project_path(change["path"])
        before = _read_file_snapshot(rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if change["action"] == "delete":
            if os.path.isfile(target):
                os.remove(target)
        else:
            with open(target, "w", encoding="utf-8", newline="") as f:
                f.write(change["after"]["content"])
        after = _read_file_snapshot(rel)
        applied.append({**change, "before": before, "applied_after": after})
    batch["status"] = "accepted"
    batch["applied_at"] = time.time()
    EDIT_STATE["undo"].append(batch)
    EDIT_STATE["redo"].clear()
    return batch


def _restore_snapshot(snapshot):
    target, _ = _safe_project_path(snapshot["path"])
    if snapshot.get("exists"):
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="") as f:
            f.write(snapshot.get("content", ""))
    elif os.path.isfile(target):
        os.remove(target)


def _is_safe_command(command):
    command = (command or "").strip().lower()
    if not command:
        return False
    dangerous_tokens = ("&&", "||", ";", "|", ">", "<", "`", "$(", "%comspec%", "../", "..\\")
    if any(token in command for token in dangerous_tokens):
        return False
    safe_patterns = (
        r"^python\s+[\w./-]+\.py(\s|$)",
        r"^python\s+-m\s+py_compile\s+[\w./-]+\.py(\s|$)",
        r"^python\s+[\w./-]+/(main|app|server)\.py(\s|$)",
        r"^python\s+-m\s+uvicorn\s+[\w.]+:(app|application)(\s|$)",
        r"^uvicorn\s+[\w.]+:(app|application)(\s|$)",
        r"^python\s+-m\s+compileall\s+[\w./-]+(\s|$)",
        r"^node\s+--check\s+[\w./-]+\.(js|mjs|cjs)(\s|$)",
        r"^npm\s+test(\s|$)",
        r"^npm\s+run\s+(dev|build|start|preview|check)(\s+--prefix\s+[\w./-]+)?(\s|$)",
        r"^pytest(\s+[\w./-]+)?(\s|$)",
        r"^flask\s+run(\s|$)",
    )
    if any(re.match(pattern, command) for pattern in safe_patterns):
        return True
    return any(command == prefix or command.startswith(prefix + " ") for prefix in SAFE_COMMAND_PREFIXES)


def _looks_like_python_web_request(text):
    lowered = (text or "").lower()
    web_words = ("fastapi", "flask", "server", "backend", "api", "localhost", "web", "arayuz", "arayÃƒÂ¼z")
    py_words = ("python", "agent.py", "main.py", "app.py", "rag.py", "yapay zeka", "gemini")
    return any(word in lowered for word in web_words) and any(word in lowered for word in py_words)


def _looks_like_js_fullstack_request(text):
    lowered = (text or "").lower()
    if any(marker in lowered for marker in ("tek dosya", "tek kod", "single file", "one file")):
        return False
    js_words = ("javascript", "node.js", "node", "express", "react", "vite", "npm", "kanban", "taskboard")
    project_words = ("proje", "project", "uygulama", "app", "klasor", "klasÃƒÆ’Ã‚Â¶r", "dizin", "frontend", "backend", "full-stack", "fullstack")
    return any(word in lowered for word in js_words) and any(word in lowered for word in project_words)


def _content_from_change(change):
    after = change.get("after", {}) if isinstance(change, dict) else {}
    return after.get("content", "") if isinstance(after, dict) else ""


def _server_quality_errors(batch):
    errors = []
    user_request = batch.get("user_request", "")

    changes = batch.get("changes", [])
    file_map = {change.get("path", "").replace("\\", "/"): _content_from_change(change) for change in changes}
    if _looks_like_js_fullstack_request(user_request):
        paths = set(file_map)
        suffixes = {path.split("/", 1)[1] if "/" in path else path for path in paths}
        required = agent._js_fullstack_required_suffixes(user_request)
        if len(paths) < 10:
            errors.append("js-fullstack: proje tek/az dosyaya indirgenmis; tam client/server dosya agaci gerekli")
        for path in paths:
            if path.lower().strip("/") == "node.js" or os.path.basename(path).lower() == "node.js":
                errors.append(f"{path}: Node/React full-stack proje root node.js dosyasina yazilamaz")
        for missing in sorted(required - suffixes):
            errors.append(f"js-fullstack: zorunlu dosya eksik: {missing}")

        server_path = next((path for path in paths if path.endswith("server/src/index.js")), "")
        server_code = file_map.get(server_path, "")
        if server_path:
            lowered = server_code.lower()
            if "express" not in lowered or "listen" not in lowered:
                errors.append(f"{server_path}: Express app ve app.listen bulunmuyor")
            if "5000" not in server_code and "process.env.port" not in lowered:
                errors.append(f"{server_path}: backend PORT=5000/process.env.PORT kullanmiyor")
            if "/api/" not in server_code and '"/api' not in server_code and "'/api" not in server_code:
                errors.append(f"{server_path}: /api route'lari eksik")
            store_suffix = agent._preferred_js_store_suffix(user_request)
            if f"./{os.path.basename(store_suffix)}" not in server_code:
                errors.append(f"{server_path}: planlanan store dosyasini import etmiyor: ./{os.path.basename(store_suffix)}")

        vite_path = next((path for path in paths if path.endswith("client/vite.config.js")), "")
        vite_code = file_map.get(vite_path, "")
        if vite_path and ("proxy" not in vite_code.lower() or "5000" not in vite_code):
            errors.append(f"{vite_path}: Vite proxy http://localhost:5000 hedefine baglanmiyor")

        api_path = next((path for path in paths if path.endswith("client/src/services/api.js")), "")
        api_code = file_map.get(api_path, "")
        if api_path and "/api" not in api_code and "5000" not in api_code:
            errors.append(f"{api_path}: frontend API base /api veya localhost:5000 degil")

        server_pkg = next((path for path in paths if path.endswith("server/package.json")), "")
        if server_pkg and "express" not in file_map.get(server_pkg, "").lower():
            errors.append(f"{server_pkg}: express bagimliligi eksik")
        if server_pkg:
            try:
                server_pkg_data = json.loads(file_map.get(server_pkg, "") or "{}")
            except Exception:
                server_pkg_data = {}
            server_deps = {}
            if isinstance(server_pkg_data, dict):
                for section in ("dependencies", "devDependencies"):
                    if isinstance(server_pkg_data.get(section), dict):
                        server_deps.update({str(k).lower(): v for k, v in server_pkg_data[section].items()})
            server_blob = "\n".join(
                file_map.get(path, "")
                for path in paths
                if "/server/src/" in ("/" + path.replace("\\", "/"))
            ).lower()
            for marker, package_name in {
                "express": "express",
                "cors": "cors",
                "dotenv": "dotenv",
                "openai": "openai",
                "@google/generative-ai": "@google/generative-ai",
            }.items():
                if marker in server_blob and package_name.lower() not in server_deps:
                    errors.append(f"{server_pkg}: server kodu {package_name} kullaniyor ama dependency eksik")
        root_pkg = next((path for path in paths if path.count("/") == 1 and path.endswith("package.json")), "")
        if root_pkg:
            try:
                root_pkg_data = json.loads(file_map.get(root_pkg, "") or "{}")
            except Exception:
                root_pkg_data = {}
            if isinstance(root_pkg_data, dict):
                scripts = root_pkg_data.get("scripts") if isinstance(root_pkg_data.get("scripts"), dict) else {}
                scripts_blob = json.dumps(scripts).lower()
                if not root_pkg_data.get("workspaces") and not all(word in scripts_blob for word in ("server", "client")):
                    errors.append(f"{root_pkg}: client/server calistirmayi koordine etmiyor")
        store_path = next((path for path in paths if agent._is_js_store_path(path)), "")
        if store_path:
            store_code = file_map.get(store_path, "").lower()
            if len(store_code.strip()) < 1200 or not all(word in store_code for word in ("list", "create", "update", "remove")):
                errors.append(f"{store_path}: task store CRUD/state mantigi eksik veya fazla kisa")
            if any(marker in store_code for marker in ("document.", "window.", "domcontentloaded", "queryselector")):
                errors.append(f"{store_path}: server store dosyasina browser/DOM kodu yazilmis")
        client_pkg = next((path for path in paths if path.endswith("client/package.json")), "")
        if client_pkg:
            client_pkg_code = file_map.get(client_pkg, "").lower()
            if "react" not in client_pkg_code or "vite" not in client_pkg_code:
                errors.append(f"{client_pkg}: react/vite bagimlilikleri eksik")
            try:
                client_pkg_data = json.loads(file_map.get(client_pkg, "") or "{}")
            except Exception:
                client_pkg_data = {}
            client_deps = {}
            if isinstance(client_pkg_data, dict):
                for section in ("dependencies", "devDependencies"):
                    if isinstance(client_pkg_data.get(section), dict):
                        client_deps.update({str(k).lower(): v for k, v in client_pkg_data[section].items()})
            client_blob = "\n".join(
                file_map.get(path, "")
                for path in paths
                if "/client/" in ("/" + path.replace("\\", "/"))
            ).lower()
            for marker, package_name in {
                "react": "react",
                "react-dom": "react-dom",
                "@vitejs/plugin-react": "@vitejs/plugin-react",
                "lucide-react": "lucide-react",
            }.items():
                if marker in client_blob and package_name.lower() not in client_deps:
                    errors.append(f"{client_pkg}: client kodu {package_name} kullaniyor ama dependency eksik")
        package_names = {}
        for pkg_path in (server_pkg, client_pkg):
            if not pkg_path:
                continue
            try:
                pkg_data = json.loads(file_map.get(pkg_path, "") or "{}")
            except Exception:
                pkg_data = {}
            name = str(pkg_data.get("name", "") if isinstance(pkg_data, dict) else "").strip()
            if not name:
                continue
            if name in package_names:
                errors.append(f"{pkg_path}: workspace package name tekrarlaniyor: {name}")
            package_names[name] = pkg_path
        return errors

    if not _looks_like_python_web_request(user_request):
        return errors

    for path, content in file_map.items():
        basename = os.path.basename(path).lower()
        if basename not in ("main.py", "app.py", "server.py"):
            continue

        lowered = content.lower()
        root = path.split("/", 1)[0] if "/" in path else ""
        if len(content.strip()) < 900:
            errors.append(f"{path}: server dosyasi Extended kalite seviyesi icin fazla kisa")
        if "fastapi" in lowered and "uvicorn.run" not in lowered:
            errors.append(f"{path}: FastAPI server icin uvicorn.run entrypoint eksik")
        if "from flask import" in lowered and ".run(" not in lowered:
            errors.append(f"{path}: Flask server icin app.run entrypoint eksik")
        if "staticfiles" in lowered and "path(__file__)" not in lowered:
            errors.append(f"{path}: static yolu Path(__file__) tabanli degil")
        if root and re.search(rf'["\']{re.escape(root)}/static', content, re.IGNORECASE):
            errors.append(f"{path}: static yolu proje adina hard-code edilmis")
        if root and re.search(rf'open\(["\']{re.escape(root)}/', content, re.IGNORECASE):
            errors.append(f"{path}: dosya okuma yolu proje adina hard-code edilmis")

        requirements_path = f"{root}/requirements.txt" if root else "requirements.txt"
        requirements = file_map.get(requirements_path, "")
        if "fastapi" in lowered and ("fastapi" not in requirements.lower() or "uvicorn" not in requirements.lower()):
            errors.append(f"{requirements_path}: FastAPI/uvicorn bagimlilikleri eksik")
        if "from flask import" in lowered and "flask" not in requirements.lower():
            errors.append(f"{requirements_path}: Flask bagimliligi eksik")

    return errors


def _desktop_quality_errors(batch):
    errors = []
    user_request = batch.get("user_request", "")
    if not _looks_like_python_desktop_request(user_request):
        return errors
    changes = batch.get("changes", []) if isinstance(batch, dict) else []
    file_map = {change.get("path", "").replace("\\", "/"): _content_from_change(change) for change in changes}
    py_paths = [path for path in file_map if path.lower().endswith(".py")]
    web_paths = [
        path for path, content in file_map.items()
        if path.lower().endswith((".html", ".css", ".js", ".jsx", ".tsx"))
        and content.strip()
    ]
    for path in web_paths:
        errors.append(f"{path}: Tkinter/Python isteginde web dosyasi uretilmemeli")
    if not py_paths:
        errors.append("desktop: Tkinter/Python istegi icin hedef .py dosyasi yok")
    for path in py_paths:
        content = file_map.get(path, "")
        lowered = content.lower()
        if "tkinter" not in lowered and "from tkinter" not in lowered and "import tkinter" not in lowered:
            errors.append(f"{path}: tkinter importu/arayuzu eksik")
        if any(marker in lowered for marker in ("<html", "document.addeventlistener", "react", "vite")):
            errors.append(f"{path}: Python dosyasina web kodu karismis")
    return errors


def _frontend_contract_errors(batch):
    errors = []
    user_request = batch.get("user_request", "") if isinstance(batch, dict) else ""
    if _looks_like_js_fullstack_request(user_request):
        return errors
    changes = batch.get("changes", []) if isinstance(batch, dict) else []
    file_map = {change.get("path", "").replace("\\", "/"): _content_from_change(change) for change in changes}
    html_text = "\n".join(content for path, content in file_map.items() if path.lower().endswith(".html"))
    css_text = "\n".join(content for path, content in file_map.items() if path.lower().endswith(".css"))
    js_text = "\n".join(content for path, content in file_map.items() if path.lower().endswith(".js"))
    py_text = "\n".join(content for path, content in file_map.items() if path.lower().endswith(".py"))

    if not html_text or not js_text:
        return errors

    html_ids = set(re.findall(r'id=["\']([^"\']+)["\']', html_text))
    html_classes = set()
    for value in re.findall(r'class=["\']([^"\']+)["\']', html_text):
        html_classes.update(part for part in value.split() if part)

    js_ids = set(re.findall(r'getElementById\(["\']([^"\']+)["\']\)', js_text))
    js_ids.update(re.findall(r'querySelector\(["\']#([^"\']+)["\']\)', js_text))
    for missing_id in sorted(js_ids - html_ids)[:8]:
        errors.append(f"frontend: JS #{missing_id} id'sini ariyor ama HTML'de yok")

    css_classes = set(re.findall(r'\.([a-zA-Z_][\w-]*)', css_text))
    css_classes = {item for item in css_classes if not item.startswith("hljs")}
    if len(css_classes) >= 6:
        overlap = css_classes & html_classes
        if len(overlap) / max(len(css_classes), 1) < 0.25:
            errors.append("frontend: CSS class'lari HTML ile eslesmiyor; sayfa default HTML gibi gorunebilir")

    if py_text:
        routes = set(re.findall(r'@app\.(?:route|get|post|put|delete|patch)\(["\']([^"\']+)["\']', py_text))
        fetches = set(re.findall(r'fetch\(["\']([^"\']+)["\']', js_text))
        for endpoint in sorted(fetches):
            if endpoint.startswith("/") and endpoint not in routes and not endpoint.startswith("/static"):
                errors.append(f"frontend: {endpoint} endpoint'i backend route listesinde yok")

        backend_keys = set(re.findall(r'["\'](response|reply|answer|message)["\']\s*:', py_text))
        frontend_keys = set(re.findall(r'data\.(response|reply|answer|message)\b', js_text))
        if "reply" in frontend_keys and "reply" not in backend_keys and "response" in backend_keys:
            errors.append("frontend: backend response donduruyor ama frontend data.reply okuyor")
        if "response" in frontend_keys and "response" not in backend_keys and "reply" in backend_keys:
            errors.append("frontend: backend reply donduruyor ama frontend data.response okuyor")

    return errors


def _final_validate_batch(batch):
    if not batch:
        return {"ok": True, "message": "No file changes"}
    errors = []
    errors.extend(_server_quality_errors(batch))
    errors.extend(_desktop_quality_errors(batch))
    errors.extend(_frontend_contract_errors(batch))
    checked = 0
    for change in batch.get("changes", []):
        if change.get("action") == "delete" or not change.get("path", "").endswith(".py"):
            continue
        target, _ = _safe_project_path(change["path"])
        if os.path.isfile(target):
            checked += 1
            try:
                py_compile.compile(target, doraise=True)
            except Exception as exc:
                errors.append(f"{change['path']}: {exc}")
    if errors:
        return {"ok": False, "message": "Final quality check found issues", "errors": errors}
    if checked:
        return {"ok": True, "message": f"{checked} Python file(s) compiled"}
    return {"ok": True, "message": "Final kontrol tamamlandi"}


def _stage_or_apply_changes(changes, auto_accept=False, user_request="", rename_conflicts=True):
    if not changes:
        return None
    changes = _retarget_changes_to_requested_root(changes, user_request=user_request)
    if rename_conflicts:
        changes = _auto_rename_conflicting_changes(changes, user_request=user_request)
    batch = {
        "plan_id": "plan_" + uuid.uuid4().hex[:12],
        "created_at": time.time(),
        "status": "pending",
        "changes": changes,
        "user_request": user_request,
        "summary": f"{len(changes)} file change(s) prepared",
    }
    with EDIT_LOCK:
        EDIT_STATE["pending"][batch["plan_id"]] = batch
        if auto_accept:
            _apply_batch(batch)
            EDIT_STATE["pending"].pop(batch["plan_id"], None)
    return batch


def _retarget_plan_to_root(plan, root):
    root = (root or "").strip("/\\")
    if not root:
        return plan
    structural_roots = {
        "client", "server", "src", "static", "public", "assets", "css", "js",
        "styles", "components", "services", "lib", "utils", "pages", "api",
    }
    throwaway_roots = {
        "gazigpt-js-app", "gazigpt_app", "gcode-project", "vanilla-web-app",
        "app", "project",
    }

    def retarget_path(path):
        clean = str(path or "").replace("\\", "/").strip("/")
        if not clean:
            return clean
        if clean == root or clean.startswith(f"{root}/"):
            return clean
        parts = clean.split("/")
        first = parts[0].lower() if parts else ""
        if len(parts) > 1 and first in throwaway_roots:
            suffix = "/".join(parts[1:])
        elif len(parts) > 1 and first not in structural_roots:
            suffix = "/".join(parts[1:])
        else:
            suffix = clean
        return f"{root}/{suffix}"

    plan["project_name"] = root
    plan["files"] = [
        {**file_info, "path": retarget_path(file_info.get("path", ""))}
        for file_info in plan.get("files", [])
        if isinstance(file_info, dict)
    ]
    folders = []
    for file_info in plan["files"]:
        folder = os.path.dirname(file_info["path"]).replace("\\", "/")
        if folder:
            folders.append(folder)
    plan["folders"] = sorted(set(folders))
    return plan


def _build_recovery_changes_from_batch(batch, errors=None):
    user_request = batch.get("user_request", "")
    if not (
        _looks_like_python_web_request(user_request)
        or _looks_like_js_fullstack_request(user_request)
        or _looks_like_python_desktop_request(user_request)
    ):
        return []
    plan = agent._normalize_code_plan(agent._fallback_code_plan(user_request), user_request)
    if not _looks_like_python_desktop_request(user_request):
        project_folder = _project_folder_for_batch(batch)
        default_root = "gazigpt-js-app" if _looks_like_js_fullstack_request(user_request) else "gazigpt_app"
        root = project_folder.get("path") or agent._requested_project_root(user_request) or default_root
        plan = _retarget_plan_to_root(plan, root)

    changes = []
    for file_info in plan.get("files", []):
        path = file_info.get("path", "")
        if not path:
            continue
        content = agent._emergency_file_content(path, plan, user_request)
        change = _build_change_from_tool({
            "action": "write",
            "path": path,
            "content": content,
        })
        if change:
            changes.append(change)
    return changes


def _auto_fix_batch(batch, validation):
    if not batch or validation.get("ok"):
        return None
    changes = _build_recovery_changes_from_batch(batch, validation.get("errors", []))
    if not changes:
        return None
    if all(_preview_stats_for_change(change) == (0, 0) for change in changes):
        return None
    repair_batch = _stage_or_apply_changes(
        changes,
        auto_accept=True,
        user_request=batch.get("user_request", "") + "\n[Auto-fix recovery]",
        rename_conflicts=False,
    )
    if repair_batch:
        repair_batch["summary"] = f"Auto-fix recovery: {len(changes)} file(s) regenerated"
    return repair_batch


# Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬ ROUTES Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/static/<path:filename>")
def serve_static(filename):
    return send_from_directory("static", filename)


@app.route("/api/image-proxy")
def image_proxy():
    """GÃƒÂ¶rsel URL'sini proxy'ler Ã¢â‚¬â€ kaynak domain gizlenir."""
    import requests as req_lib
    url = request.args.get("url", "")
    if not url:
        return jsonify({"error": "URL gerekli"}), 400
    try:
        resp = req_lib.get(url, timeout=60, stream=True)
        content_type = resp.headers.get("Content-Type", "image/png")
        return Response(
            resp.iter_content(chunk_size=8192),
            content_type=content_type,
            headers={"Cache-Control": "public, max-age=86400"}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chats/sync", methods=["GET", "PUT", "POST", "DELETE"])
def api_chats_sync():
    if request.method == "GET":
        return jsonify(_load_chat_store())

    if request.method == "DELETE":
        return jsonify(_save_chat_store(_empty_chat_store()))

    data = request.get_json(silent=True) or {}
    chats = data.get("chats", {})
    if not isinstance(chats, dict):
        return jsonify({"error": "chats must be an object"}), 400

    try:
        store = _save_chat_store({
            "current_chat_id": data.get("current_chat_id"),
            "chats": chats,
        })
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(store)


@app.route("/api/chats", methods=["GET", "POST", "DELETE"])
def api_chats():
    if request.method == "GET":
        return jsonify(_load_chat_store())

    if request.method == "DELETE":
        return jsonify(_save_chat_store(_empty_chat_store()))

    data = request.get_json(silent=True) or {}
    chat = data.get("chat", data)
    if not isinstance(chat, dict) or not chat.get("id"):
        return jsonify({"error": "chat.id is required"}), 400

    store = _load_chat_store()
    store["chats"][chat["id"]] = chat
    store["current_chat_id"] = chat["id"]
    return jsonify(_save_chat_store(store))


@app.route("/api/chats/<chat_id>", methods=["GET", "PUT", "DELETE"])
def api_chat_item(chat_id):
    store = _load_chat_store()
    if request.method == "GET":
        chat = store["chats"].get(chat_id)
        if not chat:
            return jsonify({"error": "Chat not found"}), 404
        return jsonify({"chat": chat})

    if request.method == "DELETE":
        store["chats"].pop(chat_id, None)
        if store.get("current_chat_id") == chat_id:
            store["current_chat_id"] = None
        return jsonify(_save_chat_store(store))

    data = request.get_json(silent=True) or {}
    chat = data.get("chat", data)
    if not isinstance(chat, dict):
        return jsonify({"error": "chat must be an object"}), 400
    chat["id"] = chat_id
    store["chats"][chat_id] = chat
    if data.get("make_current", True):
        store["current_chat_id"] = chat_id
    return jsonify(_save_chat_store(store))


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """Mesaj gÃƒÂ¶nder, AI yanÃ„Â±tÃ„Â± al (non-stream fallback)."""
    data = request.json or {}
    messages = data.get("messages", [])
    user_message = data.get("message", "").strip()
    file_content = data.get("file_content", "")

    model_id = data.get("model", "GaziGPT")
    model_effort = str(data.get("model_effort", "medium") or "medium").lower()
    if model_effort not in ("no", "low", "medium", "high", "xhigh"):
        model_effort = "medium"
    security_level = data.get("security_level", "safe")
    auto_authorize = bool(data.get("auto_authorize", False))
    auto_accept_edits = bool(data.get("auto_accept_edits", False))
    
    backend_model = "openai"
    if model_id == "GaziGPT Extended":
        backend_model = agent.EXTENDED_MODEL_OVERRIDE
    elif model_id == "GaziGPT Hyper":
        backend_model = agent.HYPER_MODEL_OVERRIDE

    if auto_authorize and backend_model == "extended":
        auto_accept_edits = True
        if security_level == "ask_each_step":
            security_level = "safe"

    if not user_message:
        return jsonify({"error": "Message cannot be empty."}), 400

    if file_content:
        user_message += f"\n\n--- Attached File Content ---\n{file_content}\n--- End Attached File ---"

    internal_user_message = _contextualize_followup_request(messages, user_message)
    messages.append({"role": "user", "content": internal_user_message})
    effort_prompt = (
        "Default language: English. Write all user-facing responses, summaries, "
        "generated UI copy, and code comments in English unless the user explicitly asks for another language.\n"
        f"Model effort: {model_effort}."
    )
    response_text = agent.call_llm(messages, system_prompt=agent.build_system_prompt(effort_prompt), model_override=backend_model)
    tool_results = []

    return jsonify({
        "response": response_text,
        "tool_results": tool_results,
    })


@app.route("/api/chat/stream", methods=["POST"])
def api_chat_stream():
    """Streaming SSE endpoint Ã¢â‚¬â€ token token yanÃ„Â±t gÃƒÂ¶nderir."""
    data = request.json or {}
    messages = data.get("messages", [])
    user_message = data.get("message", "").strip()
    file_content = data.get("file_content", "")
    image_ratio = data.get("image_ratio", "1:1")
    model_id = data.get("model", "GaziGPT")
    long_term_memory = data.get("long_term_memory", [])
    approval_mode = data.get("approval_mode", "ask_once")
    security_level = data.get("security_level", "safe")
    model_effort = str(data.get("model_effort", "medium") or "medium").lower()
    if model_effort not in ("no", "low", "medium", "high", "xhigh"):
        model_effort = "medium"
    auto_accept_edits = bool(data.get("auto_accept_edits", False))
    auto_authorize = bool(data.get("auto_authorize", False))
    auto_fix_enabled = bool(data.get("auto_fix_enabled", False))
    auto_fix_rounds = 4 if auto_fix_enabled else 2
    if model_effort == "no":
        auto_fix_rounds = min(auto_fix_rounds, 1)
    elif model_effort == "low":
        auto_fix_rounds = min(auto_fix_rounds, 2)
    elif model_effort == "high":
        auto_fix_rounds = max(auto_fix_rounds, 3)
    elif model_effort == "xhigh":
        auto_fix_rounds = max(auto_fix_rounds, 5)
    backend_model = "openai"
    system_prompt_ext = ""
    english_response_policy = (
        "Default language: English. Write all user-facing responses, summaries, "
        "generated UI copy, generated documentation, and code comments in English "
        "unless the user explicitly asks for another language."
    )

    if model_id == "GaziGPT":
        backend_model = "openai-fast"
    elif model_id == "GaziGPT Thinking":
        backend_model = "openai-fast"
        system_prompt_ext = (
            "You are in GaziGPT Thinking mode. For complex requests, analyze the "
            "problem silently first, then give the user a direct, clear answer. "
            "Do not include <think> tags in the response."
        )
    elif model_id == "GaziGPT Extended":
        backend_model = "extended"
        system_prompt_ext = (
            "You are in GaziGPT Extended mode. Produce answers through a multi-stage "
            "quality pipeline: clarify intent, use context, plan architecture for "
            "coding tasks, implement completely, review your own output, and return "
            "only the final high-quality result. For coding tasks, use `gazi_tool` "
            "when files should be written; do not dump long code into chat."
        )
    elif model_id == "GaziGPT Hyper":
        backend_model = "hyper"
        system_prompt_ext = (
            "You are in GaziGPT Hyper mode, the strongest coding tier.\n\n"
            "Identity rule: your name is GaziGPT Hyper. You are an independent AI "
            "assistant developed by Emir Ozcan. Do not mention provider names or "
            "underlying routing. If asked, say you are GaziGPT Hyper by Emir Ozcan."
        )

    effort_instructions = {
        "no": "Model effort: no. Do not add extra analysis; move quickly and directly.",
        "low": "Model effort: low. Make a short plan and run basic checks.",
        "medium": "Model effort: medium. Balance planning, implementation, and validation.",
        "high": "Model effort: high. Think through architecture, file relationships, and stricter quality gates.",
        "xhigh": "Model effort: xhigh. Use maximum planning and review; for coding tasks, aggressively check edge cases, integration, and auto-fix paths.",
    }
    system_prompt_ext = (english_response_policy + "\n\n" + system_prompt_ext + "\n\n" + effort_instructions[model_effort]).strip()

    if not user_message:
        return jsonify({"error": "Message cannot be empty."}), 400

    if file_content:
        user_message += f"\n\n--- Attached File Content ---\n{file_content}\n--- End Attached File ---"

    internal_user_message = _contextualize_followup_request(messages, user_message)
    messages.append({"role": "user", "content": internal_user_message})
    prompt = agent.build_system_prompt(system_prompt_ext)
    is_extended_code_request = backend_model in ["extended", "hyper"] and agent._is_coding_request(internal_user_message)
    direct_fallback_model = agent.EXTENDED_MODEL_OVERRIDE if backend_model == "extended" else (agent.HYPER_MODEL_OVERRIDE if backend_model == "hyper" else backend_model)

    def generate():
        import json as _json
        full_text = ""
        chunk_count = 0
        extended_tool_calls = []

        def emit_final_validation(batch):
            validation = _final_validate_batch(batch)
            yield f"data: {_json.dumps({'type': 'final_validation', **validation}, ensure_ascii=False)}\n\n"
            if validation.get("ok"):
                return

            yield f"data: {_json.dumps({'type': 'validation_error', 'message': validation.get('message'), 'errors': validation.get('errors', [])}, ensure_ascii=False)}\n\n"
            if not (auto_fix_enabled or auto_authorize):
                return

            yield f"data: {_json.dumps({'type': 'repair_start', 'stage': 'post_apply_auto_fix', 'message': 'Final validation found issues; Auto Pilot recovery is starting...', 'errors': validation.get('errors', [])}, ensure_ascii=False)}\n\n"
            repair_batch = _auto_fix_batch(batch, validation)
            if not repair_batch:
                yield f"data: {_json.dumps({'type': 'validation_error', 'stage': 'post_apply_auto_fix', 'message': 'Automatic recovery could not be generated for this issue'}, ensure_ascii=False)}\n\n"
                return

            yield f"data: {_json.dumps({'type': 'edit_preview', 'edit': _preview_payload(repair_batch), 'auto_applied': True}, ensure_ascii=False)}\n\n"
            repair_validation = _final_validate_batch(repair_batch)
            yield f"data: {_json.dumps({'type': 'final_validation', 'auto_fixed': True, **repair_validation}, ensure_ascii=False)}\n\n"
            if not repair_validation.get("ok"):
                yield f"data: {_json.dumps({'type': 'validation_error', 'stage': 'post_apply_auto_fix', 'message': repair_validation.get('message'), 'errors': repair_validation.get('errors', [])}, ensure_ascii=False)}\n\n"

        try:
            # Ã¢â€â‚¬Ã¢â€â‚¬ GaziGPT Extended/Hyper: Ãƒâ€¡ok aÃ…Å¸amalÃ„Â± akÃ„Â±llÃ„Â± pipeline Ã¢â€â‚¬Ã¢â€â‚¬
            if backend_model in ["extended", "hyper"]:
                phase_labels = {
                    "meta_prompt": "Clarifying the request...",
                    "idea_generation": "Generating ideas...",
                    "semantic_memory": "Searching memory...",
                    "memory": "Preparing context...",
                    "file_discovery": "Planning files...",
                    "per_file_prompting": "Preparing per-file prompts...",
                    "thinking": "Running deeper analysis...",
                    "code_architect": "Planning code architecture...",
                    "implementation": "Drafting implementation...",
                    "sequential_file_generation": "Running file agents sequentially...",
                    "stage_validation": "Running stage validation...",
                    "code_review": "Reviewing code quality...",
                    "auto_fix": "Repairing issues with auto-fix...",
                    "synthesis": "Preparing final response...",
                    "apply": "Preparing edits...",
                    "verification": "Running verification...",
                    "final_validation": "Running final validation...",
                }
                
                verification_text = ""  # Son cevap, doÃ„Å¸rulama iÃƒÂ§in
                used_fallback = False
                
                for event_type, event_data in agent.extended_pipeline_stream(
                    messages,
                    system_prompt=prompt,
                    memory_list=long_term_memory,
                    auto_fix_rounds=auto_fix_rounds,
                    model_override=agent.HYPER_MODEL_OVERRIDE if backend_model == "hyper" else agent.EXTENDED_MODEL_OVERRIDE,
                    reasoning_effort=model_effort,
                ):
                    if event_type == "phase":
                        label = phase_labels.get(event_data, f"{event_data}...")
                        yield f"data: {_json.dumps({'type': 'extended_phase', 'phase': event_data, 'label': label}, ensure_ascii=False)}\n\n"
                        yield f"data: {_json.dumps({'type': 'plan_phase', 'phase': event_data, 'label': label}, ensure_ascii=False)}\n\n"
                        yield f"data: {_json.dumps({'type': 'plan_update', 'phase': event_data, 'label': label}, ensure_ascii=False)}\n\n"
                    
                    elif event_type == "request_wait":
                        yield f"data: {_json.dumps({'type': 'request_wait', 'seconds': event_data, 'message': f'Waiting {event_data}s before the next LLM request'}, ensure_ascii=False)}\n\n"

                    elif event_type == "plan_update":
                        payload = event_data if isinstance(event_data, dict) else {"message": str(event_data)}
                        yield f"data: {_json.dumps({'type': 'plan_update', **payload}, ensure_ascii=False)}\n\n"

                    elif event_type == "stage_validation":
                        payload = event_data if isinstance(event_data, dict) else {"message": str(event_data)}
                        yield f"data: {_json.dumps({'type': 'stage_validation', **payload}, ensure_ascii=False)}\n\n"

                    elif event_type == "validation_error":
                        payload = event_data if isinstance(event_data, dict) else {"message": str(event_data)}
                        yield f"data: {_json.dumps({'type': 'validation_error', **payload}, ensure_ascii=False)}\n\n"

                    elif event_type == "repair_start":
                        payload = event_data if isinstance(event_data, dict) else {"message": str(event_data)}
                        yield f"data: {_json.dumps({'type': 'repair_start', **payload}, ensure_ascii=False)}\n\n"
                    
                    elif event_type == "ping":
                        yield f"data: {_json.dumps({'type': 'ping'})}\n\n"
                    
                    elif event_type == "chunk":
                        if "pollinations" in event_data.lower():
                            continue
                        chunk_content = _strip_extended_code_payload(event_data) if is_extended_code_request else event_data
                        if not chunk_content:
                            continue
                        full_text += chunk_content
                        verification_text += chunk_content
                        chunk_count += 1
                        yield f"data: {_json.dumps({'type': 'chunk', 'content': chunk_content}, ensure_ascii=False)}\n\n"

                    elif event_type == "tool_calls":
                        if isinstance(event_data, list):
                            extended_tool_calls = event_data
                        yield f"data: {_json.dumps({'type': 'ping'})}\n\n"
                    
                    elif event_type == "fallback":
                        used_fallback = True
                        if is_extended_code_request:
                            fallback_msg = (
                                "The Extended pipeline fell back during file generation. "
                                "Raw code was blocked from the chat response; please retry the request."
                            )
                            full_text += fallback_msg
                            chunk_count += 1
                            yield f"data: {_json.dumps({'type': 'validation_error', 'stage': 'fallback_guard', 'message': fallback_msg}, ensure_ascii=False)}\n\n"
                            yield f"data: {_json.dumps({'type': 'chunk', 'content': fallback_msg}, ensure_ascii=False)}\n\n"
                            continue
                        for chunk in agent.call_llm_stream(messages, system_prompt=prompt, model_override=direct_fallback_model):
                            if "pollinations" in chunk.lower():
                                continue
                            full_text += chunk
                            chunk_count += 1
                            yield f"data: {_json.dumps({'type': 'chunk', 'content': chunk}, ensure_ascii=False)}\n\n"
                    
                    elif event_type == "done":
                        pass
                
                # Extended pipeline bitti Ã¢â‚¬â€ eÃ„Å¸er cevap boÃ…Å¸sa fallback yap
                if not full_text.strip() and not extended_tool_calls:
                    print("[DEBUG] Extended pipeline bos cevap uretti, fallback calistiriliyor...")
                    yield f"data: {_json.dumps({'type': 'extended_phase', 'phase': 'synthesis', 'label': 'Preparing a direct response...'}, ensure_ascii=False)}\n\n"
                    if is_extended_code_request:
                        fallback_msg = (
                            "The Extended coding pipeline could not produce file edits. "
                            "Raw code was blocked from the chat response; please retry the request."
                        )
                        full_text += fallback_msg
                        chunk_count += 1
                        yield f"data: {_json.dumps({'type': 'validation_error', 'stage': 'empty_extended_pipeline', 'message': fallback_msg}, ensure_ascii=False)}\n\n"
                        yield f"data: {_json.dumps({'type': 'chunk', 'content': fallback_msg}, ensure_ascii=False)}\n\n"
                    else:
                        for chunk in agent.call_llm_stream(messages, system_prompt=prompt, model_override=direct_fallback_model):
                            if "pollinations" in chunk.lower():
                                continue
                            full_text += chunk
                            chunk_count += 1
                            yield f"data: {_json.dumps({'type': 'chunk', 'content': chunk}, ensure_ascii=False)}\n\n"
                
            else:
                # Ã¢â€â‚¬Ã¢â€â‚¬ Normal model akÃ„Â±Ã…Å¸Ã„Â± (GaziGPT ve Thinking) Ã¢â€â‚¬Ã¢â€â‚¬
                for chunk in agent.call_llm_stream(messages, system_prompt=prompt, model_override=backend_model):
                    if "pollinations" in chunk.lower():
                        continue
                    full_text += chunk
                    chunk_count += 1
                    yield f"data: {_json.dumps({'type': 'chunk', 'content': chunk}, ensure_ascii=False)}\n\n"

            print(f"[DEBUG] Stream bitti: {chunk_count} chunk, {len(full_text)} karakter")
            if full_text[:200]:
                try:
                    print(f"[DEBUG] Ilk 200 karakter: {full_text[:200]}")
                except UnicodeEncodeError:
                    print("[DEBUG] Ilk 200 karakter (Yazdirilamayan karakterler iceriyor)")

            # Stream bitti Ã¢â‚¬â€ tool call var mÃ„Â± kontrol et
            tool_matches = [_json.dumps(extended_tool_calls, ensure_ascii=False)] if extended_tool_calls else agent.extract_tool_calls(full_text)
            print(f"[DEBUG] Tool matches: {len(tool_matches)}")

            raw_file_tool_mentions = len(re.findall(r'"tool"\s*:\s*"file_manager"', full_text, flags=re.IGNORECASE))
            parsed_file_tool_count = 0
            for match_text in tool_matches:
                try:
                    parsed_match = _json.loads(match_text)
                    parsed_items = parsed_match if isinstance(parsed_match, list) else [parsed_match]
                    parsed_file_tool_count += sum(1 for item in parsed_items if isinstance(item, dict) and item.get("tool") == "file_manager")
                except Exception:
                    pass
            if raw_file_tool_mentions and parsed_file_tool_count < raw_file_tool_mentions:
                yield f"data: {_json.dumps({'type': 'repair_start', 'stage': 'tool_json', 'message': 'Repairing malformed tool JSON...'}, ensure_ascii=False)}\n\n"
                repair_prompt = (
                    "Convert the following malformed assistant response into exactly one valid gazi_tool block. "
                    "Return only a JSON array of valid tool calls inside the block. "
                    "Use workspace-relative paths only; remove /Desktop, ~/Desktop, drive letters and home folders. "
                    "Escape all quotes and newlines inside file content strings.\n\n"
                    f"User request:\n{internal_user_message}\n\n"
                    f"Malformed response:\n{full_text[:12000]}"
                )
                repair_text = ""
                for chunk in agent.call_llm_stream([{"role": "user", "content": repair_prompt}], system_prompt=prompt, model_override=direct_fallback_model, temperature=0.1):
                    repair_text += chunk
                    yield f"data: {_json.dumps({'type': 'ping'}, ensure_ascii=False)}\n\n"
                repaired_matches = agent.extract_tool_calls(repair_text)
                if repaired_matches and len(repaired_matches) >= len(tool_matches):
                    tool_matches = repaired_matches
                    yield f"data: {_json.dumps({'type': 'stage_validation', 'stage': 'tool_json_repair', 'ok': True, 'message': 'Tool JSON repaired'}, ensure_ascii=False)}\n\n"
                else:
                    yield f"data: {_json.dumps({'type': 'validation_error', 'stage': 'tool_json_repair', 'message': 'Tool JSON could not be fully repaired'}, ensure_ascii=False)}\n\n"

            # Ã¢â€â‚¬Ã¢â€â‚¬ FALLBACK: Thinking model dÃƒÂ¼Ã…Å¸ÃƒÂ¼ncede tool planladÃ„Â± ama content boÃ…Å¸ kaldÃ„Â±ysa Ã¢â€â‚¬Ã¢â€â‚¬
            if not tool_matches:
                import re as _re
                think_match = _re.search(r'<think>([\s\S]*?)</think>', full_text, _re.IGNORECASE)
                content_only = _re.sub(r'<think>[\s\S]*?</think>', '', full_text, flags=_re.IGNORECASE).strip()
                
                if think_match and len(content_only) < 20:
                    think_text = think_match.group(1).lower()
                    registered_tools = list(agent.tool_manager.tools.keys())
                    
                    for tool_name in registered_tools:
                        if tool_name in think_text:
                            print(f"[DEBUG] FALLBACK: Thinking model '{tool_name}' aracini planlamis ama content bos. Otomatik calistiriliyor...")
                            
                            if tool_name == "generate_image":
                                eng_prompt = ""
                                
                                try:
                                    import re as _re2
                                    think_content = _re.search(r'<think>([\s\S]*?)</think>', full_text, _re.IGNORECASE)
                                    if think_content:
                                        t = think_content.group(1)
                                        patterns = [
                                            r'(?:description|prompt)[:\s]+["\']([^"\']{5,})["\']',
                                            r'(?:want|need|generate|produce|create)\s+(?:a|an)\s+(.+?)(?:\.|,|image|picture|photo)',
                                            r'(?:they want|user wants?)\s+(?:a|an)\s+(.+?)(?:\.|,|image|picture)',
                                        ]
                                        for pattern in patterns:
                                            match = _re2.search(pattern, t, _re2.IGNORECASE)
                                            if match:
                                                candidate = match.group(1).strip().strip('"').strip("'")
                                                if len(candidate) >= 3 and not any(c in candidate for c in "ÃƒÂ§Ã…Å¸Ã„Å¸ÃƒÂ¼ÃƒÂ¶Ã„Â±Ãƒâ€¡Ã…ÂÃ„ÂÃƒÅ“Ãƒâ€“Ã„Â°"):
                                                    eng_prompt = f"{candidate}, digital art, highly detailed, beautiful lighting, 8K"
                                                    print(f"[DEBUG] Dusunceden prompt: {eng_prompt[:80]}")
                                                    break
                                except Exception:
                                    pass
                                
                                if not eng_prompt or len(eng_prompt) < 10:
                                    clean_msg = user_message.lower()
                                    for remove_word in ["bana", "bir", "resim", "resmi", "ÃƒÂ§iz", "ÃƒÂ§izer", "misin", "lÃƒÂ¼tfen",
                                                        "gÃƒÂ¶rsel", "oluÃ…Å¸tur", "ÃƒÂ¼ret", "yap", "fotoÃ„Å¸raf", "tablo", "en", 
                                                        "olsun", "Ã…Å¸irin", "tatlÃ„Â±", "gÃƒÂ¼zel", "harika", "muhteÃ…Å¸em"]:
                                        clean_msg = clean_msg.replace(remove_word, "")
                                    clean_msg = " ".join(clean_msg.split()).strip()
                                    
                                    if clean_msg and len(clean_msg) >= 2:
                                        eng_prompt = f"{clean_msg}, digital art, highly detailed, beautiful composition, 8K quality"
                                    else:
                                        eng_prompt = "a beautiful artistic digital illustration, vibrant colors, 8K"
                                
                                for bad in ["pollinations", "http://", "https://", "Pollinations"]:
                                    eng_prompt = eng_prompt.replace(bad, "")
                                eng_prompt = eng_prompt.strip().strip('"').strip("'")
                                if len(eng_prompt) < 10:
                                    eng_prompt = "a beautiful artistic digital illustration"
                                
                                try:
                                    print(f"[DEBUG] FALLBACK gorsel promptu: {eng_prompt[:100]}")
                                except UnicodeEncodeError:
                                    pass
                                
                                synthetic_tool_json = _json.dumps({"tool": "generate_image", "params": {"prompt": eng_prompt, "ratio": image_ratio}})
                                tool_matches = [synthetic_tool_json]
                            else:
                                synthetic_tool_json = _json.dumps({"tool": tool_name, "params": {}})
                                tool_matches = [synthetic_tool_json]
                            break

            if tool_matches:
                tool_names_start = []
                parsed_tool_calls = []
                for m in tool_matches:
                    try:
                        parsed = _json.loads(m)
                        if isinstance(parsed, dict) and "tool" in parsed:
                            if parsed.get("tool") == "file_manager" and isinstance(parsed.get("params"), dict) and parsed["params"].get("path"):
                                parsed["params"]["path"] = _normalize_project_path(parsed["params"]["path"])
                            tool_names_start.append(parsed["tool"])
                            parsed_tool_calls.append(parsed)
                        elif isinstance(parsed, list):
                            for d in parsed:
                                if isinstance(d, dict) and "tool" in d:
                                    if d.get("tool") == "file_manager" and isinstance(d.get("params"), dict) and d["params"].get("path"):
                                        d["params"]["path"] = _normalize_project_path(d["params"]["path"])
                                    tool_names_start.append(d["tool"])
                                    parsed_tool_calls.append(d)
                    except:
                        pass

                parsed_tool_calls = _guard_tool_calls_for_request(
                    parsed_tool_calls,
                    user_message,
                    internal_request=internal_user_message,
                )
                parsed_tool_calls = _retarget_tool_calls_to_requested_root(
                    parsed_tool_calls,
                    internal_user_message,
                )
                tool_names_start = [item.get("tool") for item in parsed_tool_calls if isinstance(item, dict) and item.get("tool")]
                plan_payload = _plan_payload_from_tool_calls(parsed_tool_calls)
                if plan_payload:
                    yield f"data: {_json.dumps({'type': 'plan_update', **plan_payload}, ensure_ascii=False)}\n\n"
                yield f"data: {_json.dumps({'type': 'tool_start', 'count': len(tool_names_start) or 1, 'tools': tool_names_start}, ensure_ascii=False)}\n\n"
                yield f"data: {_json.dumps({'type': 'stage_validation', 'stage': 'tool_parse', 'ok': bool(parsed_tool_calls), 'message': f'{len(parsed_tool_calls)} tool call(s) parsed'}, ensure_ascii=False)}\n\n"

                agent._current_image_ratio = image_ratio

                tool_results = []
                staged_changes = []
                blocked_commands = []
                deferred_commands = []
                has_file_edits = any(
                    isinstance(item, dict)
                    and item.get("tool") == "file_manager"
                    and item.get("params", {}).get("action") in ("write", "delete")
                    for item in parsed_tool_calls
                )

                for item in parsed_tool_calls:
                    tool_name = item.get("tool")
                    params = item.get("params", {})
                    try:
                        if tool_name == "file_manager" and params.get("action") in ("write", "delete"):
                            staged_changes.append(_build_change_from_tool(params))
                            continue

                        if tool_name == "ask_question":
                            question = params.get("question", "No question provided")
                            options = params.get("options", [])
                            yield f"data: {_json.dumps({'type': 'ask_question', 'question': question, 'options': options}, ensure_ascii=False)}\n\n"
                            tool_results.append({
                                "tool": "ask_question",
                                "params": params,
                                "result": {"success": True, "message": "A question was sent to the user. The user will reply."}
                            })
                            continue

                        if tool_name == "shell_executor":
                            command = params.get("command", "")
                            if has_file_edits:
                                deferred_commands.append(params)
                                continue
                            if security_level == "ask_each_step" and not (auto_authorize and _is_safe_command(command)):
                                blocked_commands.append(command)
                                tool_results.append({
                                    "tool": tool_name,
                                    "params": params,
                                    "result": {
                                        "success": False,
                                        "needs_permission": True,
                                        "error": "User approval is required for this command",
                                    },
                                })
                                continue
                            if security_level == "safe" and not _is_safe_command(command):
                                blocked_commands.append(command)
                                tool_results.append({
                                    "tool": tool_name,
                                    "params": params,
                                    "result": {
                                        "success": False,
                                        "needs_permission": True,
                                        "error": "Command is outside the safe allowlist",
                                    },
                                })
                                continue
                            yield f"data: {_json.dumps({'type': 'command_start', 'command': command}, ensure_ascii=False)}\n\n"
                            result = agent.tool_manager.execute_tool(tool_name, params)
                            tool_results.append({"tool": tool_name, "params": params, "result": result})
                            inner = result.get("result", result)
                            yield f"data: {_json.dumps({'type': 'command_done', 'command': command, 'result': inner}, ensure_ascii=False)}\n\n"
                            continue

                        result = agent.tool_manager.execute_tool(tool_name, params)
                        tool_results.append({"tool": tool_name, "params": params, "result": result})
                    except Exception as tool_error:
                        tool_results.append({
                            "tool": tool_name,
                            "params": params,
                            "result": {"success": False, "error": str(tool_error)},
                        })

                staged_changes = [c for c in staged_changes if c]
                quality_fixes = []
                quality_warnings = []
                if staged_changes:
                    staged_changes, quality_fixes, quality_warnings = _quality_guard_changes(
                        staged_changes,
                        user_request=internal_user_message,
                    )
                    for fix in quality_fixes:
                        yield f"data: {_json.dumps({'type': 'stage_validation', 'stage': 'local_quality_guard', 'ok': True, 'path': fix.get('path'), 'message': 'Weak file output was replaced with a local quality scaffold', 'errors': fix.get('issues', [])}, ensure_ascii=False)}\n\n"
                    for warning in quality_warnings:
                        yield f"data: {_json.dumps({'type': 'validation_error', 'stage': 'local_quality_guard', 'path': warning.get('path'), 'message': 'File quality warning', 'errors': warning.get('issues', [])}, ensure_ascii=False)}\n\n"
                edit_batch = None
                if staged_changes:
                    yield f"data: {_json.dumps({'type': 'stage_validation', 'stage': 'edit_preview', 'ok': True, 'message': f'{len(staged_changes)} file change(s) prepared'}, ensure_ascii=False)}\n\n"
                    allow_auto_apply = (auto_accept_edits or auto_authorize) and security_level in ("safe", "full_access")
                    if security_level == "ask_each_step":
                        allow_auto_apply = False
                    edit_batch = _stage_or_apply_changes(staged_changes, auto_accept=allow_auto_apply, user_request=internal_user_message)
                    edit_payload = _preview_payload(edit_batch)
                    yield f"data: {_json.dumps({'type': 'edit_preview', 'edit': edit_payload, 'auto_applied': allow_auto_apply}, ensure_ascii=False)}\n\n"
                    result_status = "applied" if allow_auto_apply else "waiting for approval"
                    tool_results.append({
                        "tool": "file_manager",
                        "params": {"action": "staged_batch", "plan_id": edit_batch["plan_id"]},
                        "result": {
                            "success": True,
                            "result": {
                                "plan_id": edit_batch["plan_id"],
                                "status": edit_batch["status"],
                                "message": f"{len(staged_changes)} file change(s) {result_status}.",
                            },
                        },
                    })

                if deferred_commands:
                    if edit_batch and edit_batch.get("status") == "accepted":
                        for params in deferred_commands:
                            command = params.get("command", "")
                            if security_level == "ask_each_step" and not (auto_authorize and _is_safe_command(command)):
                                blocked_commands.append(command)
                                tool_results.append({
                                    "tool": "shell_executor",
                                    "params": params,
                                    "result": {
                                        "success": False,
                                        "needs_permission": True,
                                        "error": "User approval is required for this command",
                                    },
                                })
                                continue
                            if security_level == "safe" and not _is_safe_command(command):
                                blocked_commands.append(command)
                                tool_results.append({
                                    "tool": "shell_executor",
                                    "params": params,
                                    "result": {
                                        "success": False,
                                        "needs_permission": True,
                                        "error": "Command is outside the safe allowlist",
                                    },
                                })
                                continue
                            yield f"data: {_json.dumps({'type': 'command_start', 'command': command}, ensure_ascii=False)}\n\n"
                            result = agent.tool_manager.execute_tool("shell_executor", params)
                            tool_results.append({"tool": "shell_executor", "params": params, "result": result})
                            inner = result.get("result", result)
                            yield f"data: {_json.dumps({'type': 'command_done', 'command': command, 'result': inner}, ensure_ascii=False)}\n\n"
                    else:
                        for params in deferred_commands:
                            command = params.get("command", "")
                            blocked_commands.append(command)
                            tool_results.append({
                                "tool": "shell_executor",
                                "params": params,
                                "result": {
                                    "success": False,
                                    "needs_permission": True,
                                    "error": "Command was not run before file edits were approved",
                                },
                            })

                if blocked_commands:
                    yield f"data: {_json.dumps({'type': 'permission_required', 'kind': 'command', 'commands': blocked_commands, 'message': 'Permission is required to run the command'}, ensure_ascii=False)}\n\n"
                
                if not tool_results:
                    processed, tool_results = agent.execute_tool_calls(full_text)

                tool_names = [tr['tool'] for tr in tool_results]
                tool_data = []
                for tr in tool_results:
                    td = {"tool": tr["tool"]}
                    res = tr.get("result", {})
                    inner = res.get("result", res)
                    if isinstance(inner, dict) and "image_url" in inner:
                        td["image_url"] = inner["image_url"]
                    if isinstance(inner, dict) and "plan_id" in inner:
                        td["plan_id"] = inner["plan_id"]
                        td["status"] = inner.get("status")
                    tool_data.append(td)

                yield f"data: {_json.dumps({'type': 'tool_done', 'tools': tool_names, 'results': tool_data}, ensure_ascii=False)}\n\n"

                has_image = any("generate_image" in tr.get("tool", "") for tr in tool_results)
                
                if has_image:
                    img_result = next((tr for tr in tool_results if tr["tool"] == "generate_image"), None)
                    if img_result:
                        img_prompt = img_result.get("params", {}).get("prompt", "")
                        confirm_msg = "Your image was generated successfully."
                        if img_prompt:
                            confirm_msg += f"\n\n**Prompt used:** {img_prompt}"
                        
                        for word in confirm_msg.split(" "):
                            yield f"data: {_json.dumps({'type': 'chunk', 'content': word + ' '}, ensure_ascii=False)}\n\n"
                else:
                    if edit_batch:
                        status_text = "applied" if edit_batch.get("status") == "accepted" else "waiting for approval"
                        actual_files = [change["path"] for change in edit_batch.get("changes", [])]
                        project_folder = _project_folder_for_batch(edit_batch)
                        project_rel = project_folder.get("path") or "."
                        project_abs = project_folder.get("absolute_path") or BASE_DIR
                        project_rel_attr = html.escape(project_rel, quote=True)
                        project_abs_label = html.escape(project_abs, quote=False)
                        summary_lines = [
                            f"{len(actual_files)} file change(s) {status_text}.",
                            "Actual file list:",
                        ] + [f"- `{path}`" for path in actual_files]
                        summary_lines.append("You can review the details in the plan panel above the input.")
                        summary_lines.append(
                            "\n"
                            f"<button type=\"button\" class=\"project-folder-link\" data-path=\"{project_rel_attr}\">"
                            f"Open project folder: {project_abs_label}"
                            "</button>"
                        )
                        deterministic_summary = "\n".join(summary_lines)
                        yield f"data: {_json.dumps({'type': 'chunk', 'content': deterministic_summary}, ensure_ascii=False)}\n\n"
                        if edit_batch.get("status") == "accepted":
                            yield from emit_final_validation(edit_batch)
                        yield f"data: {_json.dumps({'type': 'done'})}\n\n"
                        return

                    # Sentezleme fazÃ„Â± iÃƒÂ§in sadece gerekli bilgileri gÃƒÂ¶nder (HÃ„Â±z iÃƒÂ§in)
                    synthesis_msgs = [
                        {"role": "user", "content": user_message},
                        {"role": "assistant", "content": "Understood, I will handle it now."},
                    ]
                    processed_text = "Tool results:"
                    for tr in tool_results:
                        res_val = tr["result"].get("result", tr["result"])
                        if isinstance(res_val, str) and len(res_val) > 2000:
                            res_val = res_val[:2000] + "... (remaining output was saved to disk)"
                        result_json = _json.dumps(res_val, ensure_ascii=False) 
                        processed_text += f"\n\n**{tr['tool']} result:**\n```json\n{result_json}\n```"

                    synthesis_msgs.append({
                        "role": "user",
                        "content": (
                            f"[System: {processed_text}\n\n"
                            "Give the user a short, clear summary based on the technical results above. "
                            "Confirm what was done. Do not call tools again.]"
                        )
                    })

                    synthesis_sent = False
                    for chunk in agent.call_llm_stream(synthesis_msgs, system_prompt=prompt, model_override=direct_fallback_model):
                        if "pollinations" in chunk.lower():
                            continue
                        if chunk.strip():
                            synthesis_sent = True
                        yield f"data: {_json.dumps({'type': 'chunk', 'content': chunk}, ensure_ascii=False)}\n\n"

                    if not synthesis_sent:
                        if edit_batch:
                            status_text = "applied" if edit_batch.get("status") == "accepted" else "waiting for approval"
                            fallback_summary = (
                                f"{len(edit_batch.get('changes', []))} file change(s) prepared and {status_text}. "
                                "You can review the details in the plan panel above the input."
                            )
                        elif blocked_commands:
                            fallback_summary = "Permission is required to run the command. Check the permission notice in the plan panel."
                        else:
                            fallback_summary = "The operation is complete. Tool results were received."
                        yield f"data: {_json.dumps({'type': 'chunk', 'content': fallback_summary}, ensure_ascii=False)}\n\n"

                if edit_batch and edit_batch.get("status") == "accepted":
                    yield from emit_final_validation(edit_batch)

        except Exception as e:
            yield f"data: {_json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

        yield f"data: {_json.dumps({'type': 'done'})}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
            "Transfer-Encoding": "chunked",
        },
    )


@app.route("/api/edits/<plan_id>", methods=["GET"])
def api_get_edit(plan_id):
    """Pending/accepted edit batch bilgisini dondur."""
    with EDIT_LOCK:
        batch = EDIT_STATE["pending"].get(plan_id)
        if not batch:
            batch = next((b for b in reversed(EDIT_STATE["undo"]) if b.get("plan_id") == plan_id), None)
        if not batch:
            batch = next((b for b in reversed(EDIT_STATE["redo"]) if b.get("plan_id") == plan_id), None)
    if not batch:
        return jsonify({"error": "Edit batch bulunamadi"}), 404
    return jsonify(_preview_payload(batch))


@app.route("/api/edits/<plan_id>/file", methods=["GET"])
def api_get_edit_file(plan_id):
    """Bir edit batch icindeki dosyanin tam staged icerigini dondur."""
    path = request.args.get("path", "")
    with EDIT_LOCK:
        batch = EDIT_STATE["pending"].get(plan_id)
        if not batch:
            batch = next((b for b in reversed(EDIT_STATE["undo"]) if b.get("plan_id") == plan_id), None)
        if not batch:
            batch = next((b for b in reversed(EDIT_STATE["redo"]) if b.get("plan_id") == plan_id), None)
    if not batch:
        return jsonify({"error": "Edit batch bulunamadi"}), 404
    for change in batch.get("changes", []):
        if change["path"] == path:
            return jsonify({
                "plan_id": plan_id,
                "path": path,
                "action": change["action"],
                "content": change["after"]["content"],
                "before": change["before"]["content"],
            })
    return jsonify({"error": "File is not part of this edit batch"}), 404


@app.route("/api/open-folder", methods=["POST"])
def api_open_folder():
    data = request.json or {}
    folder = str(data.get("path", "") or "").strip()
    try:
        if folder in ("", ".", "./"):
            target = BASE_DIR
            rel = "."
        else:
            target, rel = _safe_project_path(folder)
            if os.path.isfile(target):
                target = os.path.dirname(target)
                rel = os.path.relpath(target, BASE_DIR).replace("\\", "/")
        target = os.path.abspath(target)
        if os.path.commonpath([BASE_DIR, target]) != BASE_DIR:
            return jsonify({"error": "Access outside the project folder was blocked"}), 400
        if not os.path.isdir(target):
            return jsonify({"error": "Folder does not exist on disk yet. Accept the edits first.", "path": rel}), 404
        os.startfile(target)
        return jsonify({"success": True, "path": rel, "absolute_path": target})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/edits/accept", methods=["POST"])
def api_accept_edit():
    data = request.json or {}
    plan_id = data.get("plan_id")
    with EDIT_LOCK:
        batch = EDIT_STATE["pending"].pop(plan_id, None)
        if not batch:
            return jsonify({"error": "Pending edit bulunamadi"}), 404
        applied = _apply_batch(batch)
    validation = _final_validate_batch(applied)
    return jsonify({"success": True, "edit": _preview_payload(applied), "validation": validation})


@app.route("/api/edits/reject", methods=["POST"])
def api_reject_edit():
    data = request.json or {}
    plan_id = data.get("plan_id")
    with EDIT_LOCK:
        batch = EDIT_STATE["pending"].pop(plan_id, None)
    if not batch:
        return jsonify({"error": "Pending edit bulunamadi"}), 404
    batch["status"] = "rejected"
    return jsonify({"success": True, "edit": _preview_payload(batch)})


@app.route("/api/edits/undo", methods=["POST"])
def api_undo_edit():
    with EDIT_LOCK:
        if not EDIT_STATE["undo"]:
            return jsonify({"error": "Geri alinacak edit yok"}), 404
        batch = EDIT_STATE["undo"].pop()
        for change in reversed(batch.get("changes", [])):
            _restore_snapshot(change["before"])
        batch["status"] = "undone"
        EDIT_STATE["redo"].append(batch)
    return jsonify({"success": True, "edit": _preview_payload(batch)})


@app.route("/api/edits/redo", methods=["POST"])
def api_redo_edit():
    with EDIT_LOCK:
        if not EDIT_STATE["redo"]:
            return jsonify({"error": "Yeniden uygulanacak edit yok"}), 404
        batch = EDIT_STATE["redo"].pop()
        _apply_batch(batch)
    validation = _final_validate_batch(batch)
    return jsonify({"success": True, "edit": _preview_payload(batch), "validation": validation})





@app.route("/api/permissions", methods=["POST"])
def api_permissions():
    """Client tarafindaki izin secimini dogrulamak icin hafif endpoint."""
    data = request.json or {}
    approval_mode = data.get("approval_mode", "ask_once")
    security_level = data.get("security_level", "safe")
    auto_authorize = bool(data.get("auto_authorize", False))
    if approval_mode not in ("ask_once", "manual", "auto"):
        return jsonify({"error": "Gecersiz approval_mode"}), 400
    if security_level not in ("ask_each_step", "safe", "full_access"):
        return jsonify({"error": "Gecersiz security_level"}), 400
    return jsonify({
        "success": True,
        "approval_mode": approval_mode,
        "security_level": security_level,
        "auto_authorize": auto_authorize,
    })



@app.route("/api/tools", methods=["GET"])
def api_tools():
    """YÃƒÂ¼klÃƒÂ¼ araÃƒÂ§ listesini dÃƒÂ¶ndÃƒÂ¼r (bilgi amaÃƒÂ§lÃ„Â±)."""
    return jsonify(agent.tool_manager.get_tool_info())


@app.route("/api/image-proxy", methods=["GET"])
def api_image_proxy():
    """Pollinations gorsellerini proxy uzerinden sun (referrer sorunu icin)."""
    import requests as req
    url = request.args.get("url", "")
    if not url or "pollinations.ai" not in url:
        return jsonify({"error": "Gecersiz URL"}), 400

    try:
        resp = req.get(url, timeout=60, stream=True, headers={
            "User-Agent": "GaziGPT/2.0",
        })
        if resp.status_code == 200:
            return Response(
                resp.content,
                mimetype=resp.headers.get("Content-Type", "image/jpeg"),
                headers={
                    "Cache-Control": "public, max-age=86400",
                    "Content-Disposition": "inline",
                },
            )
        return jsonify({"error": f"Pollinations error: {resp.status_code}"}), resp.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/analyze-image", methods=["POST"])
def api_analyze_image():
    """Gorsel analizi yap."""
    import base64
    import uuid
    import tempfile

    data = request.json or {}
    image_data = data.get("image_data", "")
    filename = data.get("filename", "image.png")

    if not image_data:
        return jsonify({"error": "Image data is empty"}), 400

    try:
        # Base64'ten dosyaya cevir
        if "," in image_data:
            image_data = image_data.split(",", 1)[1]

        img_bytes = base64.b64decode(image_data)

        # Temp dosyaya yaz
        upload_dir = os.path.join(os.path.dirname(__file__), "static", "uploads")
        os.makedirs(upload_dir, exist_ok=True)
        temp_filename = f"{uuid.uuid4().hex}_{filename}"
        filepath = os.path.join(upload_dir, temp_filename)

        with open(filepath, "wb") as f:
            f.write(img_bytes)

        # Gorsel analiz motoru
        from gradio_client import Client, handle_file

        client_vision = Client("gokaygokay/Florence-2")

        # Detayli Caption + OCR dene
        results = []

        try:
            caption_res = client_vision.predict(
                image=handle_file(filepath),
                task_prompt="Detailed Caption",
                text_input="",
                model_id="microsoft/Florence-2-large",
                api_name="/process_image"
            )
            if caption_res and caption_res[0]:
                results.append(f"[Image description]\n{caption_res[0]}")
        except Exception as ce:
            results.append(f"[Caption error: {str(ce)}]")

        try:
            ocr_res = client_vision.predict(
                image=handle_file(filepath),
                task_prompt="OCR",
                text_input="",
                model_id="microsoft/Florence-2-large",
                api_name="/process_image"
            )
            if ocr_res and ocr_res[0] and ocr_res[0].strip():
                results.append(f"[Text in image (OCR)]\n{ocr_res[0]}")
        except Exception as oe:
            pass  # OCR basarisiz olabilir, sorun degil

        # Temp dosyayi sil
        try:
            os.remove(filepath)
        except:
            pass

        description = "\n\n".join(results) if results else "The image could not be analyzed."

        return jsonify({
            "success": True,
            "description": description,
        })

    except Exception as e:
        return jsonify({"error": f"Image analysis error: {str(e)}"}), 500


@app.route("/api/voices", methods=["GET"])
def get_voices():
    try:
        # Multilingual voices from ai-by-chatgpt project
        extra_voices = [
            {'ShortName': 'en-AU-WilliamMultilingualNeural', 'FriendlyName': 'William', 'Locale': 'tr-TR', 'Gender': 'Male'},
            {'ShortName': 'en-US-AndrewMultilingualNeural', 'FriendlyName': 'Andrew', 'Locale': 'tr-TR', 'Gender': 'Male'},
            {'ShortName': 'en-US-AvaMultilingualNeural', 'FriendlyName': 'Ava', 'Locale': 'tr-TR', 'Gender': 'Female'},
            {'ShortName': 'en-US-BrianMultilingualNeural', 'FriendlyName': 'Brian', 'Locale': 'tr-TR', 'Gender': 'Male'},
            {'ShortName': 'en-US-EmmaMultilingualNeural', 'FriendlyName': 'Emma', 'Locale': 'tr-TR', 'Gender': 'Female'},
            {'ShortName': 'fr-FR-VivienneMultilingualNeural', 'FriendlyName': 'Vivienne', 'Locale': 'tr-TR', 'Gender': 'Female'},
            {'ShortName': 'fr-FR-RemyMultilingualNeural', 'FriendlyName': 'Remy', 'Locale': 'tr-TR', 'Gender': 'Male'},
            {'ShortName': 'de-DE-SeraphinaMultilingualNeural', 'FriendlyName': 'Seraphina', 'Locale': 'tr-TR', 'Gender': 'Female'},
            {'ShortName': 'de-DE-FlorianMultilingualNeural', 'FriendlyName': 'Florian', 'Locale': 'tr-TR', 'Gender': 'Male'},
            {'ShortName': 'it-IT-GiuseppeMultilingualNeural', 'FriendlyName': 'Giuseppe', 'Locale': 'tr-TR', 'Gender': 'Male'},
            {'ShortName': 'ko-KR-HyunsuMultilingualNeural', 'FriendlyName': 'Hyunsu', 'Locale': 'tr-TR', 'Gender': 'Male'},
            {'ShortName': 'pt-BR-ThalitaMultilingualNeural', 'FriendlyName': 'Thalita', 'Locale': 'tr-TR', 'Gender': 'Female'},
        ]
        return jsonify(extra_voices)
    except:
        return jsonify([])

@app.route("/api/tts")
def tts():
    text = request.args.get("text", "")
    voice = request.args.get("voice", "en-US-AvaMultilingualNeural")
    rate = request.args.get("rate", "+0%")
    pitch = request.args.get("pitch", "+0Hz")
    
    if not text:
        return Response("No text provided", status=400)

    # Handle variants
    v = voice
    r = rate
    p = pitch

    def generate():
        # Using a new loop for the streaming thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # Edge-TTS Streaming
            communicate = edge_tts.Communicate(text, v, rate=r, pitch=p)
            
            # Helper to run the async generator
            async_gen = communicate.stream()
            while True:
                try:
                    chunk = loop.run_until_complete(async_gen.__anext__())
                    if chunk["type"] == "audio":
                        yield chunk["data"]
                except StopAsyncIteration:
                    break
        except Exception as e:
            print(f"Streaming Error: {e}")
        finally:
            loop.close()

    return Response(stream_with_context(generate()), mimetype="audio/mpeg")


@app.route("/api/chat/fast", methods=["POST"])
def api_chat_fast():
    """Hizli yanit endpoint'i - dusunmeden hizlica cevap verir."""
    data = request.json or {}
    user_message = data.get("message", "").strip()

    if not user_message:
        return jsonify({"error": "Message cannot be empty."}), 400

    prompt = agent.build_system_prompt()

    def generate():
        import json as _json
        full_text = ""
        try:
            for chunk in agent.call_llm_fast_stream(user_message, system_prompt=prompt):
                if "pollinations" in chunk.lower():
                    continue
                full_text += chunk
                yield f"data: {_json.dumps({'type': 'chunk', 'content': chunk}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {_json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
        yield f"data: {_json.dumps({'type': 'done'})}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.after_request
def add_cors_headers(response):
    """Her istege CORS basliklari ekle (Harici uygulamalarin API'ye erisebilmesi icin)."""
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response



@app.route("/v1/images/generations", methods=["POST", "OPTIONS"])
def openai_v1_images_generations():
    """OpenAI uyumlu gorsel uretme endpoint'i."""
    if request.method == "OPTIONS":
        return Response(status=200)

    data = request.json or {}
    prompt = data.get("prompt", "")
    if not prompt:
        return jsonify({"error": "Prompt gerekli"}), 400

    import urllib.parse
    import time
    
    # Pollinations (Flux tabanli) gorsel uretme URL'si
    # OpenAI bazi dondurme formati bekler, url donmemiz lazim
    safe_prompt = urllib.parse.quote(prompt)
    seed = int(time.time() * 1000) % 1000000
    
    image_url = f"https://image.pollinations.ai/prompt/{safe_prompt}?model=flux&nologo=true&seed={seed}"
    
    return jsonify({
        "created": int(time.time()),
        "data": [
            {
                "url": image_url
            }
        ]
    })

# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
#  OpenAI UYUMLU API  Ã¢â‚¬â€ /v1/chat/completions
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

# API anahtarÃ„Â± ÃƒÂ§evre deÃ„Å¸iÃ…Å¸keninden alÃ„Â±nÃ„Â±r, yoksa varsayÃ„Â±lan 'gazigpt' kullanÃ„Â±lÃ„Â±r.
GAZIGPT_API_KEY = os.environ.get("GAZIGPT_API_KEY", "gazigpt")

# Model haritasÃ„Â±: API model adÃ„Â± Ã¢â€ â€™ (backend_model, system_prompt_ekleri)
API_MODELS = {
    "gazigpt": {
        "backend": "openai",
        "description": "GaziGPT Standard - balanced speed and quality",
        "system_ext": "",
    },
    "gazigpt-fast": {
        "backend": "openai-fast",
        "description": "GaziGPT Fast - fastest response",
        "system_ext": "",
    },
    "gazigpt-thinking": {
        "backend": "openai-fast",
        "description": "GaziGPT Thinking - deeper planning and analysis",
        "system_ext": (
            "Default language: English unless the user explicitly asks otherwise.\n"
            "You are in GaziGPT Thinking mode. For complex requests, analyze the "
            "problem silently first, then give the user a direct, clear answer. "
            "Do not include <think> tags in the response.\n"
            "Identity rule: you are GaziGPT, developed by Emir Ozcan."
        ),
    },
    "gazigpt-extended": {
        "backend": "extended",
        "description": "GaziGPT Extended - multi-stage quality pipeline",
        "system_ext": (
            "Default language: English unless the user explicitly asks otherwise.\n"
            "You are in GaziGPT Extended mode. Use a multi-stage quality pipeline: "
            "clarify intent, use context, plan architecture for coding tasks, "
            "implement completely, review your output, and return only the final "
            "high-quality result. For coding tasks, use `gazi_tool` when files "
            "should be written; do not dump long code into chat.\n"
            "Identity rule: you are GaziGPT, developed by Emir Ozcan."
        ),
    },
    "gazigpt-hyper": {
        "backend": agent.HYPER_MODEL_OVERRIDE,
        "description": "GaziGPT Hyper - strongest coding route",
        "system_ext": (
            "Default language: English unless the user explicitly asks otherwise.\n"
            "You are in GaziGPT Hyper mode, the strongest coding tier. Produce "
            "production-ready, complete, cross-file-consistent code. For coding "
            "tasks, do not dump long code into chat; use `gazi_tool` when files "
            "should be written.\n"
            "Identity rule: your name is GaziGPT Hyper and you were developed by Emir Ozcan. Do not mention provider names."
        ),
    },
}


def _check_api_key():
    """Authorization header'dan Bearer token kontrolÃƒÂ¼."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    token = auth[7:].strip()
    return token == GAZIGPT_API_KEY


@app.route("/v1/models", methods=["GET"])
def api_v1_models():
    """OpenAI uyumlu /v1/models Ã¢â‚¬â€ KullanÃ„Â±labilir modelleri listeler."""
    if not _check_api_key():
        return jsonify({"error": {"message": "Invalid API key", "type": "invalid_request_error", "code": "invalid_api_key"}}), 401

    model_list = []
    for model_id, info in API_MODELS.items():
        model_list.append({
            "id": model_id,
            "object": "model",
            "created": 1700000000,
            "owned_by": "gazigpt",
            "description": info["description"],
        })
    return jsonify({"object": "list", "data": model_list})


@app.route("/v1/chat/completions", methods=["POST"])
def api_v1_chat_completions():
    """OpenAI uyumlu /v1/chat/completions endpoint'i.
    
    Desteklenen modeller: gazigpt, gazigpt-fast, gazigpt-thinking, gazigpt-extended, gazigpt-hyper
    API Key: Bearer gazigpt
    Streaming: stream=true/false
    """
    # Ã¢â€â‚¬Ã¢â€â‚¬ API Key kontrolÃƒÂ¼ Ã¢â€â‚¬Ã¢â€â‚¬
    if not _check_api_key():
        return jsonify({"error": {"message": "Invalid API key. Use 'Authorization: Bearer gazigpt'", "type": "invalid_request_error", "code": "invalid_api_key"}}), 401

    data = request.json or {}
    model_id = data.get("model", "gazigpt").lower().strip()
    messages = data.get("messages", [])
    stream = data.get("stream", False)
    long_term_memory = data.get("long_term_memory", [])
    auto_fix_rounds = 4 if bool(data.get("auto_fix_enabled", False)) else 2
    temperature = data.get("temperature", None)
    max_tokens = data.get("max_tokens", None)

    # Ã¢â€â‚¬Ã¢â€â‚¬ Model doÃ„Å¸rulama Ã¢â€â‚¬Ã¢â€â‚¬
    if model_id not in API_MODELS:
        return jsonify({"error": {"message": f"Model '{model_id}' not found. Available: {', '.join(API_MODELS.keys())}", "type": "invalid_request_error", "code": "model_not_found"}}), 404

    if not messages:
        return jsonify({"error": {"message": "messages is required", "type": "invalid_request_error"}}), 400

    model_config = API_MODELS[model_id]
    backend_model = model_config["backend"]
    system_ext = model_config["system_ext"]

    # System prompt'u oluÃ…Å¸tur
    prompt = agent.build_system_prompt(system_ext)

    request_id = f"chatcmpl-{int(time.time()*1000)}"
    last_user_for_v1 = agent._last_user_message(messages)
    is_v1_extended_code_request = backend_model == "extended" and agent._is_coding_request(last_user_for_v1)
    v1_direct_fallback_model = agent.EXTENDED_MODEL_OVERRIDE if backend_model == "extended" else backend_model

    # Ã¢â€â‚¬Ã¢â€â‚¬ STREAMING MODU Ã¢â€â‚¬Ã¢â€â‚¬
    if stream:
        def stream_openai():
            import json as _json

            if backend_model == "extended":
                # Extended pipeline
                for event_type, event_data in agent.extended_pipeline_stream(
                    messages,
                    system_prompt=prompt,
                    memory_list=long_term_memory,
                    auto_fix_rounds=auto_fix_rounds,
                ):
                    if event_type == "chunk":
                        content = _strip_extended_code_payload(event_data) if is_v1_extended_code_request else event_data
                        if not content:
                            continue
                        chunk_obj = {
                            "id": request_id,
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": model_id,
                            "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
                        }
                        yield f"data: {_json.dumps(chunk_obj, ensure_ascii=False)}\n\n"
                    elif event_type == "done":
                        done_obj = {
                            "id": request_id,
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": model_id,
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        }
                        yield f"data: {_json.dumps(done_obj, ensure_ascii=False)}\n\n"
                        yield "data: [DONE]\n\n"
                    elif event_type == "fallback":
                        if is_v1_extended_code_request:
                            fallback_msg = "The Extended coding pipeline fell back; raw code was blocked from the response."
                            chunk_obj = {
                                "id": request_id,
                                "object": "chat.completion.chunk",
                                "created": int(time.time()),
                                "model": model_id,
                                "choices": [{"index": 0, "delta": {"content": fallback_msg}, "finish_reason": None}],
                            }
                            yield f"data: {_json.dumps(chunk_obj, ensure_ascii=False)}\n\n"
                            continue
                        for chunk in agent.call_llm_stream(messages, system_prompt=prompt, model_override=v1_direct_fallback_model):
                            chunk_obj = {
                                "id": request_id,
                                "object": "chat.completion.chunk",
                                "created": int(time.time()),
                                "model": model_id,
                                "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}],
                            }
                            yield f"data: {_json.dumps(chunk_obj, ensure_ascii=False)}\n\n"
                        done_obj = {
                            "id": request_id,
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": model_id,
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        }
                        yield f"data: {_json.dumps(done_obj, ensure_ascii=False)}\n\n"
                        yield "data: [DONE]\n\n"
            else:
                # Normal model stream
                for chunk in agent.call_llm_stream(messages, system_prompt=prompt, model_override=backend_model):
                    chunk_obj = {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model_id,
                        "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}],
                    }
                    yield f"data: {_json.dumps(chunk_obj, ensure_ascii=False)}\n\n"

                done_obj = {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model_id,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
                yield f"data: {_json.dumps(done_obj, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"

        return Response(
            stream_with_context(stream_openai()),
            content_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Ã¢â€â‚¬Ã¢â€â‚¬ NON-STREAMING MODU Ã¢â€â‚¬Ã¢â€â‚¬
    else:
        full_text = ""

        if backend_model == "extended":
            for event_type, event_data in agent.extended_pipeline_stream(
                messages,
                system_prompt=prompt,
                memory_list=long_term_memory,
                auto_fix_rounds=auto_fix_rounds,
            ):
                if event_type == "chunk":
                    full_text += _strip_extended_code_payload(event_data) if is_v1_extended_code_request else event_data
                elif event_type == "fallback":
                    if is_v1_extended_code_request:
                        full_text += "The Extended coding pipeline fell back; raw code was blocked from the response."
                        continue
                    for chunk in agent.call_llm_stream(messages, system_prompt=prompt, model_override=v1_direct_fallback_model):
                        full_text += chunk
        else:
            for chunk in agent.call_llm_stream(messages, system_prompt=prompt, model_override=backend_model):
                full_text += chunk

        return jsonify({
            "id": request_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_id,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": full_text},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": sum(len(m.get("content", "").split()) for m in messages),
                "completion_tokens": len(full_text.split()),
                "total_tokens": sum(len(m.get("content", "").split()) for m in messages) + len(full_text.split()),
            },
        })


@app.route("/api/config", methods=["GET"])
def api_config():
    """Logo ve konfigÃƒÂ¼rasyon bilgilerini dÃƒÂ¶ndÃƒÂ¼r."""
    return jsonify({"logo": LOGO})



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
#  OBSIDIAN VAULT API
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

@app.route("/api/vault/dashboard", methods=["GET"])
def api_vault_dashboard():
    """Obsidian Vault dashboard verileri."""
    if not agent._obsidian_bridge:
        return jsonify({"error": "Vault sistemi aktif degil"}), 503
    try:
        dashboard = agent._obsidian_bridge.get_dashboard()
        return jsonify(dashboard)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/vault/search", methods=["POST"])
def api_vault_search():
    """Vault icinde arama yapar."""
    if not agent._obsidian_bridge:
        return jsonify({"error": "Vault sistemi aktif degil"}), 503
    data = request.json or {}
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "Arama sorgusu gerekli"}), 400
    try:
        results = agent._obsidian_bridge.search_vault(query, limit=data.get("limit", 10))
        return jsonify({"results": results, "count": len(results)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/vault/notes", methods=["GET"])
def api_vault_notes():
    """Vault'taki tum notlari listeler."""
    if not agent._obsidian_bridge:
        return jsonify({"error": "Vault sistemi aktif degil"}), 503
    try:
        note_type = request.args.get("type", None)
        notes = agent._obsidian_bridge.vault.list_notes(note_type=note_type)
        return jsonify({"notes": notes, "count": len(notes)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/vault/notes/<note_id>", methods=["GET"])
def api_vault_note_detail(note_id):
    """Tek bir notun detayini dondurur."""
    if not agent._obsidian_bridge:
        return jsonify({"error": "Vault sistemi aktif degil"}), 503
    note = agent._obsidian_bridge.vault.get_note(note_id)
    if not note:
        return jsonify({"error": "Not bulunamadi"}), 404
    return jsonify(note.to_dict())


@app.route("/api/vault/chunk/start", methods=["POST"])
def api_vault_chunk_start():
    """Chunk tabanli kod uretim oturumu baslatir."""
    if not agent._obsidian_bridge:
        return jsonify({"error": "Vault sistemi aktif degil"}), 503
    data = request.json or {}
    file_path = data.get("file_path", "").strip()
    user_request = data.get("user_request", "").strip()
    estimated_chars = data.get("estimated_chars", 10000)
    if not file_path or not user_request:
        return jsonify({"error": "file_path ve user_request gerekli"}), 400
    try:
        session = agent._obsidian_bridge.plan_chunked_generation(
            file_path, user_request, estimated_chars
        )
        return jsonify(session)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/vault/chunk/prompt", methods=["POST"])
def api_vault_chunk_prompt():
    """Belirli bir chunk icin AI prompt'u dondurur."""
    if not agent._obsidian_bridge:
        return jsonify({"error": "Vault sistemi aktif degil"}), 503
    data = request.json or {}
    session_id = data.get("session_id", "")
    chunk_index = data.get("chunk_index", 0)
    prompt = agent._obsidian_bridge.get_chunk_prompt(session_id, chunk_index)
    if prompt is None:
        return jsonify({"error": "Oturum veya chunk bulunamadi"}), 404
    return jsonify({"prompt": prompt, "max_chars": 3800})


@app.route("/api/vault/chunk/save", methods=["POST"])
def api_vault_chunk_save():
    """AI'in urettigi chunk'i kaydeder."""
    if not agent._obsidian_bridge:
        return jsonify({"error": "Vault sistemi aktif degil"}), 503
    data = request.json or {}
    session_id = data.get("session_id", "")
    chunk_index = data.get("chunk_index", 0)
    content = data.get("content", "")
    if not content:
        return jsonify({"error": "Icerik gerekli"}), 400
    try:
        result = agent._obsidian_bridge.save_generated_chunk(session_id, chunk_index, content)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/vault/chunk/assemble", methods=["POST"])
def api_vault_chunk_assemble():
    """Tum chunk'lari birlestirip tam dosya olusturur."""
    if not agent._obsidian_bridge:
        return jsonify({"error": "Vault sistemi aktif degil"}), 503
    data = request.json or {}
    session_id = data.get("session_id", "")
    try:
        result = agent._obsidian_bridge.assemble_and_validate(session_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/vault/graph", methods=["GET"])
def api_vault_graph():
    """Vault bagimlilik grafini dondurur."""
    if not agent._obsidian_bridge:
        return jsonify({"error": "Vault sistemi aktif degil"}), 503
    try:
        mermaid = agent._obsidian_bridge.get_graph_visualization()
        stats = agent._obsidian_bridge.graph.get_stats()
        return jsonify({"mermaid": mermaid, "stats": stats})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/vault/quality", methods=["GET"])
def api_vault_quality():
    """Vault kalite raporunu dondurur."""
    if not agent._obsidian_bridge:
        return jsonify({"error": "Vault sistemi aktif degil"}), 503
    try:
        report = agent._obsidian_bridge.get_quality_report()
        return jsonify({"report": report})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/vault/map", methods=["GET"])
def api_vault_map():
    """Vault haritasini dondurur."""
    if not agent._obsidian_bridge:
        return jsonify({"error": "Vault sistemi aktif degil"}), 503
    try:
        vault_map = agent._obsidian_bridge.get_vault_map()
        return jsonify({"map": vault_map})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/vault/import", methods=["POST"])
def api_vault_import():
    """Proje dosyalarini vault'a import eder."""
    if not agent._obsidian_bridge:
        return jsonify({"error": "Vault sistemi aktif degil"}), 503
    try:
        imported = agent._obsidian_bridge.vault.import_project()
        agent._obsidian_bridge.graph.rebuild()
        agent._obsidian_bridge.indexer.rebuild()
        return jsonify({
            "success": True,
            "imported_count": len(imported),
            "notes": [{"title": n.title, "chars": len(n.content)} for n in imported],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬ GCODE v2 API ENDPOINTS Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬

@app.route("/api/tier/status", methods=["GET"])
def api_tier_status():
    """Katmanli model sisteminin durumunu dondurur."""
    if not agent._tier_router:
        return jsonify({"active": False, "message": "Tier sistemi aktif degil"})
    try:
        status = agent._tier_router.get_status()
        return jsonify({"active": True, **status})
    except Exception as e:
        return jsonify({"active": False, "error": str(e)}), 500


@app.route("/api/tier/colors", methods=["GET"])
def api_tier_colors():
    """Tier'a ait renk semasini dondurur."""
    tier_name = request.args.get("tier", "core")
    if not agent._tier_router:
        return jsonify({"accent": "#8a2be2", "accent2": "#20d6a5", "bg": "#111318"})
    return jsonify(agent._tier_router.get_colors(tier_name))


@app.route("/api/contracts", methods=["GET"])
def api_contracts():
    """Tum kontrat hafizasini dondurur."""
    if not agent._contract_memory:
        return jsonify({"active": False, "message": "ContractMemory aktif degil"})
    try:
        return jsonify({
            "active": True,
            "stats": agent._contract_memory.get_stats(),
            "contracts": agent._contract_memory.get_all_contracts(),
        })
    except Exception as e:
        return jsonify({"active": False, "error": str(e)}), 500


@app.route("/api/contracts/search", methods=["GET"])
def api_contracts_search():
    """Kontratlar arasinda arama yapar."""
    query = request.args.get("q", "")
    if not agent._contract_memory or not query:
        return jsonify({"results": []})
    results = agent._contract_memory.search_contracts(query)
    return jsonify({"results": results})


@app.route("/api/contracts/validate", methods=["GET"])
def api_contracts_validate():
    """Capraz dosya kontrat tutarlilik kontrolu yapar."""
    if not agent._contract_memory:
        return jsonify({"ok": True, "issues": [], "message": "ContractMemory aktif degil"})
    issues = agent._contract_memory.validate_all()
    return jsonify({
        "ok": len(issues) == 0,
        "issue_count": len(issues),
        "issues": issues,
    })


@app.route("/api/proxy/status", methods=["GET"])
def api_proxy_status():
    """Proxy havuzu durumunu dondurur."""
    if not agent._tier_router:
        return jsonify({"active": False, "direct_mode": True})
    return jsonify({
        "active": True,
        **agent._tier_router.proxy_pool.get_status(),
    })


@app.route("/api/proxy/add", methods=["POST"])
def api_proxy_add():
    """Proxy havuzuna yeni proxy ekler."""
    if not agent._tier_router:
        return jsonify({"error": "Tier sistemi aktif degil"}), 503
    data = request.json or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "Proxy URL gerekli"}), 400
    ok = agent._tier_router.proxy_pool.add_proxy(url, source="api")
    return jsonify({"success": ok, "status": agent._tier_router.proxy_pool.get_status()})



# Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬ BAÃ…ÂLATMA Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬

def open_browser():
    time.sleep(1.5)
    webbrowser.open("http://localhost:5000")


if __name__ == "__main__":
    print("=" * 50)
    print("  GCode AI IDE - v2.0")
    print("  URL: http://localhost:5000")
    print(f"  Active tools: {len(agent.tool_manager.tools)}")
    print("  Press Ctrl+C to stop")
    print("=" * 50)

    if os.environ.get("GCODE_NO_BROWSER") != "1":
        threading.Thread(target=open_browser, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=False)

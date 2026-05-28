"""
ObsidianVault - Ana Vault Motoru

Tüm notları, kod parçalarını ve bağlantıları yöneten merkezi motor.
Obsidian benzeri wiki-link sistemi ile notlar arası bağlantı kurar.

Vault Yapısı:
    vault_data/
    ├── notes/          # Markdown notlar
    ├── chunks/         # Kod parçaları (chunk'lar)
    ├── templates/      # Kod şablonları
    ├── metadata/       # Not meta bilgileri (JSON)
    ├── assembled/      # Birleştirilmiş son dosyalar
    └── index.json      # Ana indeks dosyası
"""

import os
import json
import time
import re
import hashlib
import shutil
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple


class VaultNote:
    """Vault içindeki tek bir not/kod parçasını temsil eder."""

    def __init__(
        self,
        note_id: str,
        title: str,
        content: str,
        note_type: str = "code",
        tags: Optional[List[str]] = None,
        links: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.note_id = note_id
        self.title = title
        self.content = content
        self.note_type = note_type  # code, doc, template, chunk, config
        self.tags = tags or []
        self.links = links or []  # [[linked_note_id]] formatında bağlantılar
        self.metadata = metadata or {}
        self.created_at = time.time()
        self.updated_at = time.time()
        self.version = 1
        self.content_hash = self._compute_hash()

    def _compute_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()[:16]

    def update_content(self, new_content: str) -> None:
        self.content = new_content
        self.updated_at = time.time()
        self.version += 1
        self.content_hash = self._compute_hash()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "note_id": self.note_id,
            "title": self.title,
            "content": self.content,
            "note_type": self.note_type,
            "tags": self.tags,
            "links": self.links,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
            "content_hash": self.content_hash,
            "char_count": len(self.content),
            "line_count": len(self.content.splitlines()),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VaultNote":
        note = cls(
            note_id=data["note_id"],
            title=data["title"],
            content=data["content"],
            note_type=data.get("note_type", "code"),
            tags=data.get("tags", []),
            links=data.get("links", []),
            metadata=data.get("metadata", {}),
        )
        note.created_at = data.get("created_at", time.time())
        note.updated_at = data.get("updated_at", time.time())
        note.version = data.get("version", 1)
        note.content_hash = data.get("content_hash", note._compute_hash())
        return note

    def to_markdown(self) -> str:
        """Obsidian uyumlu Markdown formatında export eder."""
        frontmatter = [
            "---",
            f"id: {self.note_id}",
            f"title: {self.title}",
            f"type: {self.note_type}",
            f"tags: [{', '.join(self.tags)}]",
            f"version: {self.version}",
            f"chars: {len(self.content)}",
            f"hash: {self.content_hash}",
            f"created: {time.strftime('%Y-%m-%d %H:%M', time.localtime(self.created_at))}",
            f"updated: {time.strftime('%Y-%m-%d %H:%M', time.localtime(self.updated_at))}",
            "---",
            "",
        ]
        sections = ["\n".join(frontmatter)]

        if self.links:
            sections.append("## Bağlantılar")
            for link in self.links:
                sections.append(f"- [[{link}]]")
            sections.append("")

        sections.append(f"## {self.title}")
        sections.append("")

        if self.note_type == "code":
            ext = self.metadata.get("language", "python")
            sections.append(f"```{ext}")
            sections.append(self.content)
            sections.append("```")
        else:
            sections.append(self.content)

        return "\n".join(sections)


class ObsidianVault:
    """
    Obsidian benzeri bilgi yönetim sistemi.
    
    Kod parçalarını, notları ve bağlantıları yönetir.
    AI agent'ın 4096 karakter sınırını aşmasını sağlar.
    """

    # Vault kök dizini
    DEFAULT_VAULT_DIR = "vault_data"
    
    # Alt dizinler
    SUBDIRS = ["notes", "chunks", "templates", "metadata", "assembled", "history"]
    
    # 4096'dan güvenli bir şekilde altında kalmak için chunk limiti
    CHUNK_CHAR_LIMIT = 3800
    
    # Maksimum versiyon geçmişi
    MAX_HISTORY = 50

    def __init__(self, vault_dir: Optional[str] = None):
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.vault_dir = os.path.join(
            self.base_dir, vault_dir or self.DEFAULT_VAULT_DIR
        )
        self.notes: Dict[str, VaultNote] = {}
        self.index: Dict[str, Any] = {}
        self._ensure_vault_structure()
        self._load_index()

    def _ensure_vault_structure(self) -> None:
        """Vault dizin yapısını oluşturur."""
        os.makedirs(self.vault_dir, exist_ok=True)
        for subdir in self.SUBDIRS:
            os.makedirs(os.path.join(self.vault_dir, subdir), exist_ok=True)

    def _index_path(self) -> str:
        return os.path.join(self.vault_dir, "index.json")

    def _load_index(self) -> None:
        """Vault indeksini diskten yükler."""
        index_file = self._index_path()
        if os.path.isfile(index_file):
            try:
                with open(index_file, "r", encoding="utf-8") as f:
                    self.index = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.index = self._create_fresh_index()
        else:
            self.index = self._create_fresh_index()
            self._save_index()

        # Notları diskten yükle
        for note_id, meta in self.index.get("notes", {}).items():
            note_path = self._note_path(note_id)
            if os.path.isfile(note_path):
                try:
                    with open(note_path, "r", encoding="utf-8") as f:
                        note_data = json.load(f)
                    self.notes[note_id] = VaultNote.from_dict(note_data)
                except (json.JSONDecodeError, IOError):
                    pass

    def _create_fresh_index(self) -> Dict[str, Any]:
        return {
            "vault_version": "1.0.0",
            "created_at": time.time(),
            "updated_at": time.time(),
            "notes": {},
            "tags": {},
            "graph_edges": [],
            "stats": {
                "total_notes": 0,
                "total_chunks": 0,
                "total_chars": 0,
                "total_lines": 0,
            },
        }

    def _save_index(self) -> None:
        """İndeksi diske yazar."""
        self.index["updated_at"] = time.time()
        self.index["stats"] = self._compute_stats()
        try:
            with open(self._index_path(), "w", encoding="utf-8") as f:
                json.dump(self.index, f, ensure_ascii=False, indent=2)
        except IOError as e:
            print(f"[VAULT] Indeks kaydedilemedi: {e}")

    def _note_path(self, note_id: str) -> str:
        return os.path.join(self.vault_dir, "metadata", f"{note_id}.json")

    def _content_path(self, note_id: str, subdir: str = "notes") -> str:
        return os.path.join(self.vault_dir, subdir, f"{note_id}.md")

    def _compute_stats(self) -> Dict[str, int]:
        total_chars = sum(len(n.content) for n in self.notes.values())
        total_lines = sum(len(n.content.splitlines()) for n in self.notes.values())
        chunk_count = sum(1 for n in self.notes.values() if n.note_type == "chunk")
        return {
            "total_notes": len(self.notes),
            "total_chunks": chunk_count,
            "total_chars": total_chars,
            "total_lines": total_lines,
        }

    # ── CRUD Operasyonları ────────────────────────────────────

    def create_note(
        self,
        title: str,
        content: str,
        note_type: str = "code",
        tags: Optional[List[str]] = None,
        links: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        note_id: Optional[str] = None,
    ) -> VaultNote:
        """Yeni bir not oluşturur ve vault'a ekler."""
        if note_id is None:
            slug = re.sub(r"[^a-zA-Z0-9_-]", "_", title.lower()).strip("_")[:40]
            note_id = f"{slug}_{hashlib.md5(title.encode()).hexdigest()[:8]}"

        if note_id in self.notes:
            # Mevcut notu güncelle
            return self.update_note(note_id, content=content, title=title, tags=tags)

        note = VaultNote(
            note_id=note_id,
            title=title,
            content=content,
            note_type=note_type,
            tags=tags,
            links=links,
            metadata=metadata,
        )
        self.notes[note_id] = note

        # Metadata JSON olarak kaydet
        self._save_note_to_disk(note)

        # Markdown olarak kaydet
        md_path = self._content_path(note_id, "notes" if note_type != "chunk" else "chunks")
        try:
            with open(md_path, "w", encoding="utf-8", newline="") as f:
                f.write(note.to_markdown())
        except IOError:
            pass

        # İndeksi güncelle
        self.index.setdefault("notes", {})[note_id] = {
            "title": title,
            "type": note_type,
            "tags": tags or [],
            "chars": len(content),
            "hash": note.content_hash,
            "updated_at": note.updated_at,
        }

        # Tag indeksini güncelle
        for tag in (tags or []):
            self.index.setdefault("tags", {}).setdefault(tag, [])
            if note_id not in self.index["tags"][tag]:
                self.index["tags"][tag].append(note_id)

        self._save_index()
        return note

    def _save_note_to_disk(self, note: VaultNote) -> None:
        """Not verisini JSON olarak diske yazar."""
        note_path = self._note_path(note.note_id)
        try:
            with open(note_path, "w", encoding="utf-8") as f:
                json.dump(note.to_dict(), f, ensure_ascii=False, indent=2)
        except IOError as e:
            print(f"[VAULT] Not kaydedilemedi {note.note_id}: {e}")

    def update_note(
        self,
        note_id: str,
        content: Optional[str] = None,
        title: Optional[str] = None,
        tags: Optional[List[str]] = None,
        links: Optional[List[str]] = None,
    ) -> Optional[VaultNote]:
        """Mevcut bir notu günceller, eski versiyonu history'e kaydeder."""
        note = self.notes.get(note_id)
        if note is None:
            return None

        # Eski versiyonu history'e kaydet
        self._save_history(note)

        if content is not None:
            note.update_content(content)
        if title is not None:
            note.title = title
        if tags is not None:
            note.tags = tags
        if links is not None:
            note.links = links

        note.updated_at = time.time()
        self._save_note_to_disk(note)

        # Markdown dosyasını güncelle
        md_subdir = "chunks" if note.note_type == "chunk" else "notes"
        md_path = self._content_path(note_id, md_subdir)
        try:
            with open(md_path, "w", encoding="utf-8", newline="") as f:
                f.write(note.to_markdown())
        except IOError:
            pass

        # İndeksi güncelle
        self.index.setdefault("notes", {})[note_id] = {
            "title": note.title,
            "type": note.note_type,
            "tags": note.tags,
            "chars": len(note.content),
            "hash": note.content_hash,
            "updated_at": note.updated_at,
        }
        self._save_index()
        return note

    def _save_history(self, note: VaultNote) -> None:
        """Notun eski versiyonunu history dizinine kaydeder."""
        history_dir = os.path.join(self.vault_dir, "history", note.note_id)
        os.makedirs(history_dir, exist_ok=True)

        version_file = os.path.join(history_dir, f"v{note.version}.json")
        try:
            with open(version_file, "w", encoding="utf-8") as f:
                json.dump(note.to_dict(), f, ensure_ascii=False, indent=2)
        except IOError:
            pass

        # Eski versiyonları temizle
        versions = sorted(Path(history_dir).glob("v*.json"))
        while len(versions) > self.MAX_HISTORY:
            try:
                versions[0].unlink()
            except OSError:
                pass
            versions = versions[1:]

    def get_note(self, note_id: str) -> Optional[VaultNote]:
        """ID ile not getirir."""
        return self.notes.get(note_id)

    def delete_note(self, note_id: str) -> bool:
        """Notu vault'tan siler."""
        note = self.notes.pop(note_id, None)
        if note is None:
            return False

        # Disk dosyalarını sil
        for path in [
            self._note_path(note_id),
            self._content_path(note_id, "notes"),
            self._content_path(note_id, "chunks"),
        ]:
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass

        # İndeksten sil
        self.index.get("notes", {}).pop(note_id, None)
        for tag_notes in self.index.get("tags", {}).values():
            if note_id in tag_notes:
                tag_notes.remove(note_id)
        self._save_index()
        return True

    # ── Arama ve Sorgulama ────────────────────────────────────

    def search(
        self,
        query: str,
        note_type: Optional[str] = None,
        tags: Optional[List[str]] = None,
        limit: int = 20,
    ) -> List[VaultNote]:
        """Vault içinde arama yapar."""
        query_lower = query.lower()
        query_tokens = set(query_lower.split())
        results: List[Tuple[float, VaultNote]] = []

        for note in self.notes.values():
            if note_type and note.note_type != note_type:
                continue
            if tags and not any(tag in note.tags for tag in tags):
                continue

            # Skor hesapla
            score = 0.0
            title_lower = note.title.lower()
            content_lower = note.content.lower()

            # Başlık eşleşmesi (yüksek ağırlık)
            if query_lower in title_lower:
                score += 10.0
            for token in query_tokens:
                if token in title_lower:
                    score += 3.0

            # İçerik eşleşmesi
            content_matches = content_lower.count(query_lower)
            score += min(content_matches * 1.5, 8.0)
            for token in query_tokens:
                token_count = content_lower.count(token)
                score += min(token_count * 0.5, 3.0)

            # Tag eşleşmesi
            for tag in note.tags:
                if tag.lower() in query_lower:
                    score += 5.0

            if score > 0:
                results.append((score, note))

        results.sort(key=lambda x: x[0], reverse=True)
        return [note for _, note in results[:limit]]

    def get_notes_by_tag(self, tag: str) -> List[VaultNote]:
        """Belirli bir tag'e sahip tüm notları getirir."""
        note_ids = self.index.get("tags", {}).get(tag, [])
        return [self.notes[nid] for nid in note_ids if nid in self.notes]

    def get_notes_by_type(self, note_type: str) -> List[VaultNote]:
        """Belirli bir tipteki tüm notları getirir."""
        return [n for n in self.notes.values() if n.note_type == note_type]

    def get_linked_notes(self, note_id: str) -> List[VaultNote]:
        """Bir notun bağlı olduğu tüm notları getirir."""
        note = self.notes.get(note_id)
        if note is None:
            return []
        return [self.notes[lid] for lid in note.links if lid in self.notes]

    # ── Vault İstatistikleri ──────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Vault istatistiklerini döndürür."""
        stats = self._compute_stats()
        stats["vault_dir"] = self.vault_dir
        stats["types"] = {}
        for note in self.notes.values():
            stats["types"][note.note_type] = stats["types"].get(note.note_type, 0) + 1
        stats["tags"] = {
            tag: len(ids) for tag, ids in self.index.get("tags", {}).items()
        }
        stats["largest_note"] = max(
            ((n.note_id, len(n.content)) for n in self.notes.values()),
            key=lambda x: x[1],
            default=("none", 0),
        )
        return stats

    def list_notes(self, note_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Tüm notların özet listesini döndürür."""
        summaries = []
        for note in self.notes.values():
            if note_type and note.note_type != note_type:
                continue
            summaries.append({
                "note_id": note.note_id,
                "title": note.title,
                "type": note.note_type,
                "tags": note.tags,
                "chars": len(note.content),
                "lines": len(note.content.splitlines()),
                "version": note.version,
                "updated_at": note.updated_at,
            })
        summaries.sort(key=lambda x: x["updated_at"], reverse=True)
        return summaries

    # ── Bulk Operasyonlar ─────────────────────────────────────

    def import_file(
        self,
        file_path: str,
        tags: Optional[List[str]] = None,
        note_type: str = "code",
    ) -> Optional[VaultNote]:
        """Bir dosyayı vault'a import eder."""
        if not os.path.isfile(file_path):
            return None
        
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except IOError:
            return None

        filename = os.path.basename(file_path)
        ext = os.path.splitext(filename)[1].lower()
        language_map = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".html": "html", ".css": "css", ".json": "json",
            ".md": "markdown", ".txt": "text",
        }
        metadata = {
            "source_file": file_path,
            "language": language_map.get(ext, ext.lstrip(".")),
            "extension": ext,
        }
        all_tags = list(set((tags or []) + [metadata["language"], "imported"]))

        return self.create_note(
            title=filename,
            content=content,
            note_type=note_type,
            tags=all_tags,
            metadata=metadata,
        )

    def import_project(
        self, 
        project_dir: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> List[VaultNote]:
        """Tüm proje dosyalarını vault'a import eder."""
        project_dir = project_dir or self.base_dir
        skip_dirs = {".git", "__pycache__", ".venv", "venv", "node_modules", 
                     "dist", "build", "vault_data", "obsidian_vault"}
        skip_ext = {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", 
                    ".mp3", ".mp4", ".exe", ".dll", ".so"}
        
        imported = []
        for root, dirs, filenames in os.walk(project_dir):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for filename in sorted(filenames):
                ext = os.path.splitext(filename)[1].lower()
                if ext in skip_ext:
                    continue
                file_path = os.path.join(root, filename)
                rel_path = os.path.relpath(file_path, project_dir).replace("\\", "/")
                note = self.import_file(
                    file_path,
                    tags=(tags or []) + ["project", rel_path.split("/")[0] if "/" in rel_path else "root"],
                )
                if note:
                    imported.append(note)
        
        return imported

    def export_vault_map(self) -> str:
        """Vault'un tüm yapısını gösterir (Obsidian graph view benzeri)."""
        lines = [
            "# [VAULT] GaziGPT Obsidian Vault Map",
            f"**Toplam Not:** {len(self.notes)}",
            f"**Toplam Karakter:** {sum(len(n.content) for n in self.notes.values()):,}",
            "",
            "## Notlar",
        ]
        
        for note_type in ["code", "chunk", "template", "doc", "config"]:
            notes = self.get_notes_by_type(note_type)
            if notes:
                lines.append(f"\n### {note_type.upper()} ({len(notes)})")
                for note in notes:
                    links_str = " -> ".join(f"[[{l}]]" for l in note.links) if note.links else ""
                    tags_str = " ".join(f"#{t}" for t in note.tags)
                    lines.append(
                        f"- **{note.title}** ({len(note.content):,} chars, v{note.version}) "
                        f"{tags_str} {links_str}"
                    )
        
        # Tag özeti
        lines.extend(["", "## Tag Dağılımı"])
        for tag, note_ids in sorted(self.index.get("tags", {}).items()):
            lines.append(f"- #{tag}: {len(note_ids)} not")
        
        return "\n".join(lines)

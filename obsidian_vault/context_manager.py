"""
ContextManager - AI Bağlam Yönetimi ve Bellek Optimizasyonu

AI agent'ın chunk tabanlı kod üretirken bağlamını yönetir.
4096 sınırı içinde maksimum bilgi aktarımını sağlar.
"""

import os
import re
import json
import time
from typing import Optional, Dict, List, Any, Tuple

from obsidian_vault.vault_core import ObsidianVault, VaultNote
from obsidian_vault.chunk_writer import ChunkWriter, ChunkStrategy
from obsidian_vault.assembler import CodeAssembler
from obsidian_vault.indexer import VaultIndexer


class GenerationContext:
    """AI'ın bir chunk üretirken kullanacağı bağlam paketi."""

    def __init__(
        self,
        file_path: str,
        chunk_index: int,
        total_chunks: int,
        chunk_type: str,
        chunk_label: str,
        max_output_chars: int = 3800,
    ):
        self.file_path = file_path
        self.chunk_index = chunk_index
        self.total_chunks = total_chunks
        self.chunk_type = chunk_type
        self.chunk_label = chunk_label
        self.max_output_chars = max_output_chars
        
        # Bağlam bileşenleri
        self.project_structure: str = ""
        self.previous_chunk_tail: str = ""
        self.next_chunk_head: str = ""
        self.related_code: str = ""
        self.user_request: str = ""
        self.quality_rules: str = ""
        self.existing_content: str = ""

    def build_prompt(self) -> str:
        """AI'a gönderilecek tam prompt'u oluşturur."""
        parts = [
            "## GaziGPT Obsidian Chunk Generator",
            "",
            f"**Dosya:** `{self.file_path}`",
            f"**Chunk:** {self.chunk_index + 1}/{self.total_chunks} — {self.chunk_label}",
            f"**Tip:** {self.chunk_type}",
            f"**Maksimum Çıktı:** {self.max_output_chars} karakter",
            "",
        ]

        if self.user_request:
            parts.extend([
                "### Kullanıcı İsteği",
                self.user_request[:500],
                "",
            ])

        if self.project_structure:
            parts.extend([
                "### Proje Yapısı",
                self.project_structure[:400],
                "",
            ])

        if self.previous_chunk_tail:
            parts.extend([
                "### Önceki Chunk (Son Satırlar)",
                "```",
                self.previous_chunk_tail[:300],
                "```",
                "",
            ])

        if self.next_chunk_head:
            parts.extend([
                "### Sonraki Chunk (İlk Satırlar)",
                "```",
                self.next_chunk_head[:300],
                "```",
                "",
            ])

        if self.related_code:
            parts.extend([
                "### İlgili Kod Referansları",
                self.related_code[:600],
                "",
            ])

        if self.existing_content:
            parts.extend([
                "### Mevcut İçerik (Güncellenmesi Gereken)",
                "```",
                self.existing_content[:800],
                "```",
                "",
            ])

        parts.extend([
            "### Kurallar",
            self.quality_rules or "- Tam, çalışır, import'ları eksiksiz kod yaz",
            "",
            f"### Çıktı ({self.max_output_chars} karakter sınırı)",
            "Sadece bu chunk'ın kodunu üret. Markdown fencing kullanma.",
            "Önceki ve sonraki chunk ile uyumlu olmalı.",
        ])

        return "\n".join(parts)

    def char_budget_remaining(self) -> int:
        """Prompt'un ne kadar yer kapladığını ve kalan bütçeyi hesaplar."""
        prompt = self.build_prompt()
        # Toplam bağlam penceresi (örn: 4096) - prompt uzunluğu
        total_budget = 4096
        return max(0, total_budget - len(prompt))


class ContextManager:
    """
    AI'ın chunk bazlı kod üretmesi için bağlam yönetimi.
    
    İş Akışı:
        1. Dosya planı oluştur (hangi chunk'lar lazım)
        2. Her chunk için bağlam paketi hazırla
        3. AI chunk üretir (max 3800 karakter)
        4. Chunk vault'a kaydedilir
        5. Tüm chunk'lar tamamlandığında birleştir
    """

    def __init__(self, vault: ObsidianVault):
        self.vault = vault
        self.chunk_writer = ChunkWriter(vault)
        self.assembler = CodeAssembler(vault)
        self.indexer = VaultIndexer(vault)
        
        # Aktif üretim oturumları
        self._sessions: Dict[str, Dict[str, Any]] = {}

    # ── Oturum Yönetimi ───────────────────────────────────────

    def start_generation_session(
        self,
        file_path: str,
        user_request: str,
        estimated_chars: int = 10000,
        strategy: str = ChunkStrategy.AUTO,
    ) -> Dict[str, Any]:
        """
        Yeni bir kod üretim oturumu başlatır.
        
        Dosyayı kaç chunk'a bölmek gerektiğini hesaplar
        ve her chunk için bir plan oluşturur.
        """
        max_chunk_chars = self.chunk_writer.MAX_CHUNK_CHARS
        estimated_chunks = max(1, (estimated_chars + max_chunk_chars - 1) // max_chunk_chars)
        
        # Dosya uzantısına göre chunk tipleri belirle
        ext = os.path.splitext(file_path)[1].lower()
        chunk_plan = self._plan_chunks(file_path, ext, estimated_chunks, user_request)

        session_id = f"session_{int(time.time() * 1000)}"
        session = {
            "session_id": session_id,
            "file_path": file_path,
            "user_request": user_request,
            "strategy": strategy,
            "total_chunks": len(chunk_plan),
            "completed_chunks": 0,
            "chunk_plan": chunk_plan,
            "status": "active",
            "created_at": time.time(),
        }
        
        self._sessions[session_id] = session
        return session

    def _plan_chunks(
        self, file_path: str, ext: str, count: int, user_request: str
    ) -> List[Dict[str, Any]]:
        """Dosya türüne göre chunk planı oluşturur."""
        if ext == ".py":
            return self._plan_python_chunks(file_path, count, user_request)
        elif ext in (".js", ".ts"):
            return self._plan_js_chunks(file_path, count, user_request)
        elif ext == ".html":
            return self._plan_html_chunks(file_path, count, user_request)
        elif ext == ".css":
            return self._plan_css_chunks(file_path, count, user_request)
        else:
            return [
                {
                    "index": i,
                    "type": "segment",
                    "label": f"Bölüm {i + 1}",
                    "status": "pending",
                    "content": "",
                }
                for i in range(count)
            ]

    def _plan_python_chunks(
        self, file_path: str, count: int, user_request: str
    ) -> List[Dict[str, Any]]:
        """Python dosyası için chunk planı."""
        plan = [
            {"index": 0, "type": "imports", "label": "Import ve konfigürasyon", "status": "pending", "content": ""},
        ]
        
        # İstek analizine göre ek chunk'lar
        request_lower = user_request.lower()
        has_class = any(w in request_lower for w in ["class", "sınıf", "sinif", "nesne", "object"])
        has_api = any(w in request_lower for w in ["api", "endpoint", "route", "flask", "fastapi"])
        has_agent = any(w in request_lower for w in ["agent", "ajan", "ai", "llm", "model"])
        
        remaining = count - 1
        
        if has_class or has_agent:
            plan.append({"index": len(plan), "type": "class_header", "label": "Sınıf tanımı ve __init__", "status": "pending", "content": ""})
            remaining -= 1
        
        if has_api:
            plan.append({"index": len(plan), "type": "routes", "label": "API route'ları", "status": "pending", "content": ""})
            remaining -= 1
        
        for i in range(max(0, remaining)):
            plan.append({
                "index": len(plan),
                "type": "body",
                "label": f"Fonksiyon grubu {i + 1}",
                "status": "pending",
                "content": "",
            })
        
        plan.append({"index": len(plan), "type": "footer", "label": "Entrypoint ve son kodlar", "status": "pending", "content": ""})
        
        return plan

    def _plan_js_chunks(self, file_path: str, count: int, user_request: str) -> List[Dict[str, Any]]:
        """JavaScript dosyası için chunk planı."""
        plan = [
            {"index": 0, "type": "imports", "label": "Import ve değişkenler", "status": "pending", "content": ""},
        ]
        for i in range(max(1, count - 2)):
            plan.append({
                "index": len(plan),
                "type": "body",
                "label": f"Fonksiyon bloğu {i + 1}",
                "status": "pending",
                "content": "",
            })
        plan.append({"index": len(plan), "type": "footer", "label": "Event listener'lar ve başlatma", "status": "pending", "content": ""})
        return plan

    def _plan_html_chunks(self, file_path: str, count: int, user_request: str) -> List[Dict[str, Any]]:
        """HTML dosyası için chunk planı."""
        plan = [
            {"index": 0, "type": "head", "label": "<!DOCTYPE>, <head> ve meta", "status": "pending", "content": ""},
        ]
        for i in range(max(1, count - 2)):
            plan.append({
                "index": len(plan),
                "type": "body_section",
                "label": f"<body> bölüm {i + 1}",
                "status": "pending",
                "content": "",
            })
        plan.append({"index": len(plan), "type": "scripts", "label": "<script> ve kapatma tag'leri", "status": "pending", "content": ""})
        return plan

    def _plan_css_chunks(self, file_path: str, count: int, user_request: str) -> List[Dict[str, Any]]:
        """CSS dosyası için chunk planı."""
        plan = [
            {"index": 0, "type": "variables", "label": "CSS değişkenleri ve reset", "status": "pending", "content": ""},
        ]
        for i in range(max(1, count - 2)):
            plan.append({
                "index": len(plan),
                "type": "styles",
                "label": f"Stil bloğu {i + 1}",
                "status": "pending",
                "content": "",
            })
        plan.append({"index": len(plan), "type": "responsive", "label": "Media query'ler ve responsive", "status": "pending", "content": ""})
        return plan

    # ── Bağlam Hazırlama ──────────────────────────────────────

    def get_generation_context(
        self, session_id: str, chunk_index: int
    ) -> Optional[GenerationContext]:
        """
        Belirli bir chunk için AI bağlam paketi hazırlar.
        
        Bu paket, AI'ın doğru chunk'ı üretmesi için gereken
        tüm bilgiyi içerir.
        """
        session = self._sessions.get(session_id)
        if not session:
            return None

        plan = session["chunk_plan"]
        if chunk_index < 0 or chunk_index >= len(plan):
            return None

        chunk_info = plan[chunk_index]

        ctx = GenerationContext(
            file_path=session["file_path"],
            chunk_index=chunk_index,
            total_chunks=len(plan),
            chunk_type=chunk_info["type"],
            chunk_label=chunk_info["label"],
        )

        ctx.user_request = session["user_request"]

        # Proje yapısı
        ctx.project_structure = self._get_project_structure()

        # Önceki chunk'ın son satırları
        if chunk_index > 0:
            prev_content = plan[chunk_index - 1].get("content", "")
            if prev_content:
                prev_lines = prev_content.splitlines()
                ctx.previous_chunk_tail = "\n".join(prev_lines[-8:])

        # Sonraki chunk'ın planı
        if chunk_index < len(plan) - 1:
            next_info = plan[chunk_index + 1]
            ctx.next_chunk_head = f"[Sonraki chunk: {next_info['type']} — {next_info['label']}]"

        # İlgili vault notları
        related = self.indexer.search(session["user_request"], limit=3)
        if related:
            related_parts = []
            for score, note in related:
                preview = note.content[:200].replace("\n", " ")
                related_parts.append(f"- {note.title}: {preview}")
            ctx.related_code = "\n".join(related_parts)

        # Mevcut içerik (güncelleme durumunda)
        if chunk_info.get("content"):
            ctx.existing_content = chunk_info["content"]

        # Kalite kuralları
        ctx.quality_rules = self._get_quality_rules(chunk_info["type"])

        return ctx

    def _get_project_structure(self) -> str:
        """Proje yapısının kısa özetini döndürür."""
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        skip_dirs = {".git", "__pycache__", ".venv", "venv", "node_modules", "vault_data"}
        files = []
        for root, dirs, filenames in os.walk(base):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for f in sorted(filenames)[:5]:
                rel = os.path.relpath(os.path.join(root, f), base).replace("\\", "/")
                files.append(f"- {rel}")
            if len(files) > 30:
                break
        return "\n".join(files[:30])

    def _get_quality_rules(self, chunk_type: str) -> str:
        """Chunk tipine göre kalite kurallarını döndürür."""
        rules = {
            "imports": "- Tüm gerekli import'ları ekle\n- Kullanılmayan import ekleme\n- Sıralama: stdlib -> third-party -> local",
            "class_header": "- Sınıf docstring'i ekle\n- __init__ metodunu eksiksiz yaz\n- Type hint kullan",
            "function": "- Fonksiyon docstring'i ekle\n- Error handling yap\n- Return type belirt",
            "routes": "- Her route için error handling\n- CORS header'ları\n- Input validation",
            "body": "- Placeholder/TODO bırakma\n- Tam çalışır kod yaz\n- İsimlendirme tutarlı olsun",
            "footer": "- if __name__ == '__main__' ekle\n- Entrypoint fonksiyonlarını çağır",
            "head": "- DOCTYPE, charset, viewport meta\n- SEO meta tag'leri\n- CSS/font link'leri",
            "body_section": "- Semantic HTML kullan\n- Accessibility (aria) attribute'ları\n- Responsive yapı",
            "scripts": "- DOMContentLoaded event\n- Error handling\n- Modüler fonksiyonlar",
            "variables": "- CSS custom properties (:root)\n- Reset/normalize\n- Typography",
            "styles": "- BEM veya tutarlı naming\n- Responsive düşün\n- Geçişler/animasyonlar",
            "responsive": "- Mobile-first yaklaşım\n- Breakpoint'ler: 480px, 768px, 1024px, 1200px",
        }
        return rules.get(chunk_type, "- Tam, çalışır, hatasız kod yaz\n- Placeholder bırakma")

    # ── Chunk Kaydetme ────────────────────────────────────────

    def save_chunk(self, session_id: str, chunk_index: int, content: str) -> bool:
        """AI'ın ürettiği chunk'ı kaydeder."""
        session = self._sessions.get(session_id)
        if not session:
            return False

        plan = session["chunk_plan"]
        if chunk_index < 0 or chunk_index >= len(plan):
            return False

        # Karakter sınırı kontrolü
        if len(content) > self.chunk_writer.MAX_CHUNK_CHARS:
            print(f"[CONTEXT] Uyarı: Chunk {chunk_index} sınırı aşıyor ({len(content)} > {self.chunk_writer.MAX_CHUNK_CHARS})")

        plan[chunk_index]["content"] = content
        plan[chunk_index]["status"] = "completed"
        session["completed_chunks"] = sum(1 for c in plan if c["status"] == "completed")

        # Vault'a kaydet
        file_path = session["file_path"]
        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", os.path.basename(file_path))[:30]
        chunk_id = f"{slug}_chunk_{chunk_index:03d}"

        self.vault.create_note(
            title=f"{os.path.basename(file_path)} [{plan[chunk_index]['label']}]",
            content=content,
            note_type="chunk",
            tags=["chunk", plan[chunk_index]["type"], f"file:{os.path.basename(file_path)}"],
            metadata={
                "session_id": session_id,
                "parent_file": file_path,
                "chunk_index": chunk_index,
                "chunk_type": plan[chunk_index]["type"],
            },
            note_id=chunk_id,
        )

        return True

    def is_session_complete(self, session_id: str) -> bool:
        """Oturumun tüm chunk'ları tamamlanmış mı kontrol eder."""
        session = self._sessions.get(session_id)
        if not session:
            return False
        return all(c["status"] == "completed" for c in session["chunk_plan"])

    def assemble_session(self, session_id: str) -> Optional[str]:
        """
        Oturumdaki tüm chunk'ları birleştirir ve tam dosyayı döndürür.
        """
        session = self._sessions.get(session_id)
        if not session:
            return None

        parts = []
        for chunk_info in session["chunk_plan"]:
            content = chunk_info.get("content", "")
            if content:
                parts.append(content)

        assembled = "\n\n".join(parts)

        # Assembled dizinine kaydet
        file_path = session["file_path"]
        filename = os.path.basename(file_path)
        assembled_path = os.path.join(self.vault.vault_dir, "assembled", filename)
        try:
            os.makedirs(os.path.dirname(assembled_path), exist_ok=True)
            with open(assembled_path, "w", encoding="utf-8", newline="") as f:
                f.write(assembled)
        except IOError:
            pass

        session["status"] = "assembled"
        return assembled

    # ── Yardımcılar ───────────────────────────────────────────

    def get_session_status(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Oturum durumunu döndürür."""
        session = self._sessions.get(session_id)
        if not session:
            return None

        return {
            "session_id": session["session_id"],
            "file_path": session["file_path"],
            "status": session["status"],
            "total_chunks": session["total_chunks"],
            "completed_chunks": session["completed_chunks"],
            "progress": f"{session['completed_chunks']}/{session['total_chunks']}",
            "is_complete": self.is_session_complete(session_id),
            "chunks": [
                {
                    "index": c["index"],
                    "type": c["type"],
                    "label": c["label"],
                    "status": c["status"],
                    "chars": len(c.get("content", "")),
                }
                for c in session["chunk_plan"]
            ],
        }

    def list_active_sessions(self) -> List[Dict[str, Any]]:
        """Aktif oturumları listeler."""
        return [
            self.get_session_status(sid)
            for sid in self._sessions
            if self._sessions[sid]["status"] == "active"
        ]

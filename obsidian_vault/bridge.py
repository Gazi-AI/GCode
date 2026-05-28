"""
Obsidian Vault Entegrasyonu - Agent ve App için köprü modül.

Bu modül, Obsidian Vault sistemini mevcut GaziGPT pipeline'ına
entegre eder. Agent'ın Extended pipeline'ında chunk tabanlı
kod üretme yeteneği kazandırır.
"""

import os
import json
import time
import re
from typing import Optional, Dict, List, Any, Tuple

from obsidian_vault.vault_core import ObsidianVault
from obsidian_vault.chunk_writer import ChunkWriter, ChunkStrategy
from obsidian_vault.contract_layer import ContractLayer
from obsidian_vault.assembler import CodeAssembler
from obsidian_vault.graph import DependencyGraph
from obsidian_vault.indexer import VaultIndexer
from obsidian_vault.context_manager import ContextManager
from obsidian_vault.quality_gate import QualityGate


class ObsidianBridge:
    """
    GaziGPT ↔ Obsidian Vault arasındaki köprü.
    
    Bu sınıf:
        1. Vault'u başlatır ve yönetir
        2. Agent'ın chunk bazlı kod üretmesini sağlar
        3. Üretilen parçaları birleştirir
        4. Kalite kontrolü yapar
        5. Proje dosyalarını otomatik indeksler
    """

    def __init__(self):
        self.vault = ObsidianVault()
        self.chunk_writer = ChunkWriter(self.vault)
        self.assembler = CodeAssembler(self.vault)
        self.graph = DependencyGraph(self.vault)
        self.indexer = VaultIndexer(self.vault)
        self.context_manager = ContextManager(self.vault)
        self.quality_gate = QualityGate(self.vault)
        self.contract_layer = ContractLayer(vault=self.vault)

        # İlk başlatmada proje dosyalarını indeksle
        self._auto_index_project()

    def _auto_index_project(self) -> None:
        """Proje dosyalarını otomatik vault'a import eder."""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        # Sadece ana kaynak dosyaları import et
        key_files = ["agent.py", "app.py", "main.py"]
        for filename in key_files:
            filepath = os.path.join(base_dir, filename)
            if os.path.isfile(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    
                    # Büyük dosyaları chunk'la
                    if len(content) > self.chunk_writer.MAX_CHUNK_CHARS:
                        self.chunk_writer.write_chunks_to_vault(
                            content, filename, tags=["project", "auto-indexed"]
                        )
                    else:
                        self.vault.create_note(
                            title=filename,
                            content=content,
                            note_type="code",
                            tags=["project", "python", "auto-indexed"],
                            metadata={"source_file": filepath, "language": "python"},
                        )
                except IOError:
                    pass

        # Static dosyaları da ekle
        static_dir = os.path.join(base_dir, "static")
        if os.path.isdir(static_dir):
            for root, dirs, files in os.walk(static_dir):
                for f in files:
                    ext = os.path.splitext(f)[1].lower()
                    if ext in (".html", ".css", ".js"):
                        filepath = os.path.join(root, f)
                        rel_path = os.path.relpath(filepath, base_dir).replace("\\", "/")
                        try:
                            with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
                                content = fh.read()
                            self.vault.create_note(
                                title=rel_path,
                                content=content,
                                note_type="code",
                                tags=["project", "static", ext.lstrip(".")],
                                metadata={"source_file": filepath, "language": ext.lstrip(".")},
                            )
                        except IOError:
                            pass

        # Grafı yeniden oluştur
        self.graph.rebuild()
        # İndeksi yeniden oluştur
        self.indexer.rebuild()

        stats = self.vault.get_stats()
        print(f"[OBSIDIAN] Vault hazır: {stats['total_notes']} not, {stats['total_chars']:,} karakter, {stats.get('total_chunks', 0)} chunk")

    # ── Chunk Tabanlı Kod Üretimi ─────────────────────────────

    def should_use_chunks(self, estimated_chars: int) -> bool:
        """Bu dosya chunk'lanmalı mı kontrol eder."""
        return estimated_chars > self.chunk_writer.MAX_CHUNK_CHARS

    def plan_chunked_generation(
        self,
        file_path: str,
        user_request: str,
        estimated_chars: int = 10000,
    ) -> Dict[str, Any]:
        """
        Chunk tabanlı kod üretim planı oluşturur.
        
        Returns:
            Session bilgisi (chunk planı dahil)
        """
        session = self.context_manager.start_generation_session(
            file_path=file_path,
            user_request=user_request,
            estimated_chars=estimated_chars,
        )
        return session

    def get_chunk_prompt(self, session_id: str, chunk_index: int) -> Optional[str]:
        """Belirli bir chunk için AI prompt'u döndürür."""
        ctx = self.context_manager.get_generation_context(session_id, chunk_index)
        if ctx is None:
            return None
        return ctx.build_prompt()

    def save_generated_chunk(self, session_id: str, chunk_index: int, content: str) -> Dict[str, Any]:
        """AI'ın ürettiği chunk'ı kaydeder ve kalite kontrolü yapar."""
        # Kaydet
        success = self.context_manager.save_chunk(session_id, chunk_index, content)
        
        # Kalite kontrolü
        session = self.context_manager._sessions.get(session_id, {})
        file_path = session.get("file_path", "unknown")
        chunk_info = session.get("chunk_plan", [{}])[chunk_index] if chunk_index < len(session.get("chunk_plan", [])) else {}
        
        quality = self.quality_gate.check_chunk(
            content, file_path,
            chunk_id=f"chunk_{chunk_index}",
            chunk_type=chunk_info.get("type", "body"),
        )

        return {
            "success": success,
            "chunk_index": chunk_index,
            "chars": len(content),
            "quality": quality.to_dict(),
            "is_session_complete": self.context_manager.is_session_complete(session_id),
        }

    def assemble_and_validate(self, session_id: str) -> Dict[str, Any]:
        """
        Tüm chunk'ları birleştirir ve son kalite kontrolü yapar.
        
        Returns:
            {
                "content": "tam dosya içeriği",
                "quality": QualityResult,
                "file_path": "dosya yolu",
                "stats": {...}
            }
        """
        assembled = self.context_manager.assemble_session(session_id)
        if assembled is None:
            return {"error": "Oturum bulunamadı veya tamamlanmamış"}

        session = self.context_manager._sessions.get(session_id, {})
        file_path = session.get("file_path", "unknown")

        quality = self.quality_gate.check_assembled_file(assembled, file_path)

        return {
            "content": assembled,
            "file_path": file_path,
            "quality": quality.to_dict(),
            "stats": {
                "total_chars": len(assembled),
                "total_lines": len(assembled.splitlines()),
                "chunks_used": session.get("total_chunks", 0),
            },
        }

    # ── Vault Sorguları ───────────────────────────────────────

    def search_vault(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Vault'ta arama yapar."""
        results = self.indexer.search(query, limit=limit)
        return [
            {
                "note_id": note.note_id,
                "title": note.title,
                "score": round(score, 3),
                "type": note.note_type,
                "chars": len(note.content),
                "tags": note.tags,
                "preview": note.content[:200],
            }
            for score, note in results
        ]

    def get_vault_context_for_request(self, user_request: str) -> str:
        """
        Kullanıcı isteğine en uygun vault içeriğini bağlam olarak döndürür.
        Agent'ın sistem promptuna eklenebilir.
        """
        results = self.indexer.search(user_request, limit=5)
        if not results:
            return ""

        context_parts = ["[Obsidian Vault Bağlamı — İlgili Kod/Not Referansları]"]
        for score, note in results:
            if score < 0.1:
                continue
            preview = note.content[:300].replace("\n", " ").strip()
            context_parts.append(
                f"  - {note.title} ({note.note_type}, {len(note.content)} chars): {preview}"
            )

        return "\n".join(context_parts) if len(context_parts) > 1 else ""

    # ── Raporlar ──────────────────────────────────────────────

    def get_dashboard(self) -> Dict[str, Any]:
        """Vault dashboard verilerini döndürür."""
        vault_stats = self.vault.get_stats()
        graph_stats = self.graph.get_stats()
        index_stats = self.indexer.get_index_stats()

        return {
            "vault": vault_stats,
            "graph": graph_stats,
            "index": index_stats,
            "notes": self.vault.list_notes(),
            "top_terms": self.indexer.get_top_terms(20),
        }

    def get_graph_visualization(self) -> str:
        """Mermaid graf diyagramını döndürür."""
        return self.graph.to_mermaid()

    def get_quality_report(self) -> str:
        """Tam kalite raporunu döndürür."""
        return self.quality_gate.full_quality_report()

    def get_vault_map(self) -> str:
        """Vault haritasını döndürür."""
        return self.vault.export_vault_map()

    # ── Kontrat Katmanı API ───────────────────────────────────

    def save_contract(self, file_path: str, source_code: str) -> Optional[str]:
        """
        Bir dosyanın kontratını çıkarır ve vault'a kaydeder.
        Dosya yazıldığında otomatik çağrılır.
        
        Returns:
            Vault note_id veya None
        """
        contracts = self.contract_layer.extract_contracts(source_code, file_path)
        note_id = self.contract_layer.save_to_vault(contracts)
        if note_id:
            print(f"[OBSIDIAN] Kontrat kaydedildi: {file_path} → {note_id}")
        return note_id

    def get_contracts_for_file(self, file_path: str) -> Optional[Dict[str, Any]]:
        """
        Belirli bir dosyanın kontratlarını döndürür.
        """
        contracts = self.contract_layer.get_contracts(file_path)
        if contracts:
            return contracts.to_dict()
        return None

    def get_cross_file_prompt(self, target_file: str) -> str:
        """
        Bir dosya yazılırken diğer dosyaların kontratlarını
        kompakt prompt olarak döndürür.
        AI prompt'una eklenecek — dosyalar arası uyuşmazlığı sıfırlar.
        """
        return self.contract_layer.build_cross_file_prompt(target_file)

    def validate_cross_file_contracts(self) -> List[Dict[str, Any]]:
        """
        Tüm kontratlar arasındaki tutarlılığı kontrol eder.
        Frontend-backend uyuşmazlıklarını tespit eder.
        
        Returns:
            Uyuşmazlık listesi (boş ise tutarlı)
        """
        issues = self.contract_layer.validate_consistency()
        if issues:
            print(f"[OBSIDIAN] Kontrat uyuşmazlıkları: {len(issues)} adet")
            for issue in issues[:3]:
                print(f"  - {issue.get('type', 'unknown')}: {issue.get('message', '')}")
        return issues

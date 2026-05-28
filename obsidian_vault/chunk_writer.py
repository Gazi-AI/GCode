"""
ChunkWriter - Büyük Kodları Parçalara Bölen Akıllı Sistem

4096 karakter sınırını aşmamak için kodları anlamlı parçalara böler.
Her chunk bağımsız bir birim olarak saklanır ve daha sonra birleştirilir.

Chunk Stratejileri:
    1. FUNCTION_BASED: Fonksiyon/method bazında böler
    2. CLASS_BASED: Sınıf bazında böler
    3. BLOCK_BASED: Mantıksal bloklar bazında böler (import, config, routes vs.)
    4. LINE_BASED: Sabit satır sayısına göre böler (son çare)
"""

import re
import os
import json
import hashlib
import time
from typing import Optional, Dict, List, Any, Tuple

from obsidian_vault.vault_core import ObsidianVault, VaultNote


class ChunkStrategy:
    """Kod bölme stratejisi."""
    FUNCTION_BASED = "function_based"
    CLASS_BASED = "class_based"
    BLOCK_BASED = "block_based"
    LINE_BASED = "line_based"
    AUTO = "auto"


class CodeChunk:
    """Bir kod parçasını temsil eder."""

    def __init__(
        self,
        chunk_id: str,
        parent_id: str,
        sequence: int,
        content: str,
        chunk_type: str = "body",
        label: str = "",
        dependencies: Optional[List[str]] = None,
    ):
        self.chunk_id = chunk_id
        self.parent_id = parent_id
        self.sequence = sequence
        self.content = content
        self.chunk_type = chunk_type  # header, imports, class, function, body, footer
        self.label = label
        self.dependencies = dependencies or []
        self.char_count = len(content)
        self.line_count = len(content.splitlines())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "parent_id": self.parent_id,
            "sequence": self.sequence,
            "content": self.content,
            "chunk_type": self.chunk_type,
            "label": self.label,
            "dependencies": self.dependencies,
            "char_count": self.char_count,
            "line_count": self.line_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CodeChunk":
        return cls(
            chunk_id=data["chunk_id"],
            parent_id=data["parent_id"],
            sequence=data["sequence"],
            content=data["content"],
            chunk_type=data.get("chunk_type", "body"),
            label=data.get("label", ""),
            dependencies=data.get("dependencies", []),
        )


class ChunkWriter:
    """
    Büyük kod dosyalarını akıllıca parçalara böler.
    Her parça 4096 karakter sınırının altında kalır.
    """

    # Güvenli chunk boyutu (4096 - prompt/response overhead)
    MAX_CHUNK_CHARS = 3800
    
    # Minimum chunk boyutu (çok küçük parçalar birleştirilir)
    MIN_CHUNK_CHARS = 200

    def __init__(self, vault: ObsidianVault):
        self.vault = vault

    def split_code(
        self,
        code: str,
        file_path: str,
        strategy: str = ChunkStrategy.AUTO,
        max_chunk_chars: Optional[int] = None,
    ) -> List[CodeChunk]:
        """
        Kodu akıllıca parçalara böler.
        
        Args:
            code: Bölünecek kod
            file_path: Dosya yolu (dil tespiti için)
            strategy: Bölme stratejisi
            max_chunk_chars: Maksimum chunk boyutu
            
        Returns:
            CodeChunk listesi
        """
        max_chars = max_chunk_chars or self.MAX_CHUNK_CHARS
        
        # Kod yeterince kısaysa bölme
        if len(code) <= max_chars:
            return [self._make_chunk(code, file_path, 0, "complete", "Tam dosya")]

        # Otomatik strateji seçimi
        if strategy == ChunkStrategy.AUTO:
            strategy = self._detect_best_strategy(code, file_path)

        if strategy == ChunkStrategy.FUNCTION_BASED:
            chunks = self._split_by_functions(code, file_path, max_chars)
        elif strategy == ChunkStrategy.CLASS_BASED:
            chunks = self._split_by_classes(code, file_path, max_chars)
        elif strategy == ChunkStrategy.BLOCK_BASED:
            chunks = self._split_by_blocks(code, file_path, max_chars)
        else:
            chunks = self._split_by_lines(code, file_path, max_chars)

        # Çok küçük chunk'ları birleştir
        return self._merge_tiny_chunks(chunks, max_chars)

    def _detect_best_strategy(self, code: str, file_path: str) -> str:
        """Kod yapısına göre en iyi bölme stratejisini seçer."""
        ext = os.path.splitext(file_path)[1].lower()
        
        if ext in (".py",):
            class_count = len(re.findall(r"^class\s+\w+", code, re.MULTILINE))
            func_count = len(re.findall(r"^(?:def|async\s+def)\s+\w+", code, re.MULTILINE))
            
            if class_count >= 2:
                return ChunkStrategy.CLASS_BASED
            if func_count >= 3:
                return ChunkStrategy.FUNCTION_BASED
            return ChunkStrategy.BLOCK_BASED
            
        if ext in (".js", ".ts"):
            func_count = len(re.findall(
                r"(?:^|\n)\s*(?:function|const|let|var|export\s+(?:default\s+)?(?:function|class|const))\s+\w+",
                code,
            ))
            class_count = len(re.findall(r"(?:^|\n)\s*(?:export\s+)?class\s+\w+", code))
            
            if class_count >= 2:
                return ChunkStrategy.CLASS_BASED
            if func_count >= 3:
                return ChunkStrategy.FUNCTION_BASED
            return ChunkStrategy.BLOCK_BASED
            
        if ext in (".html",):
            return ChunkStrategy.BLOCK_BASED
            
        if ext in (".css",):
            return ChunkStrategy.BLOCK_BASED
            
        return ChunkStrategy.LINE_BASED

    def _make_chunk(
        self, content: str, file_path: str, seq: int, chunk_type: str, label: str
    ) -> CodeChunk:
        """Yeni bir chunk nesnesi oluşturur."""
        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", os.path.basename(file_path))[:30]
        chunk_id = f"{slug}_chunk_{seq:03d}"
        parent_id = re.sub(r"[^a-zA-Z0-9_-]", "_", file_path)[:60]
        return CodeChunk(
            chunk_id=chunk_id,
            parent_id=parent_id,
            sequence=seq,
            content=content,
            chunk_type=chunk_type,
            label=label,
        )

    def _split_by_functions(self, code: str, file_path: str, max_chars: int) -> List[CodeChunk]:
        """Python fonksiyon/method bazında böler."""
        chunks: List[CodeChunk] = []
        lines = code.splitlines(keepends=True)
        
        # Import ve üst seviye kod bölümünü bul
        header_end = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped and not stripped.startswith(("#", "import ", "from ", '"""', "'''", '"""')):
                if not stripped.startswith(("@", "def ", "class ", "async ")):
                    header_end = i
                else:
                    break
        
        if header_end > 0:
            header = "".join(lines[:header_end])
            if header.strip():
                chunks.append(self._make_chunk(header, file_path, len(chunks), "imports", "Import ve üst seviye kod"))

        # Fonksiyonları bul
        func_pattern = re.compile(r"^((?:@\w+.*\n)*(?:def|async\s+def)\s+\w+)", re.MULTILINE)
        func_starts = [(m.start(), m.group(1).split("def ")[-1].split("(")[0].strip()) 
                       for m in func_pattern.finditer(code)]
        
        if not func_starts:
            return self._split_by_lines(code, file_path, max_chars)

        for idx, (start, func_name) in enumerate(func_starts):
            end = func_starts[idx + 1][0] if idx + 1 < len(func_starts) else len(code)
            func_code = code[start:end].rstrip()
            
            if len(func_code) > max_chars:
                # Çok büyük fonksiyonu satır bazında böl
                sub_chunks = self._split_by_lines(func_code, file_path, max_chars)
                for sc in sub_chunks:
                    sc.label = f"{func_name} (parça {sc.sequence + 1})"
                    sc.sequence = len(chunks)
                    chunks.append(sc)
            else:
                chunks.append(self._make_chunk(func_code, file_path, len(chunks), "function", func_name))

        return chunks

    def _split_by_classes(self, code: str, file_path: str, max_chars: int) -> List[CodeChunk]:
        """Sınıf bazında böler."""
        chunks: List[CodeChunk] = []
        
        class_pattern = re.compile(r"^class\s+(\w+)", re.MULTILINE)
        class_starts = [(m.start(), m.group(1)) for m in class_pattern.finditer(code)]
        
        if not class_starts:
            return self._split_by_functions(code, file_path, max_chars)

        # Sınıf öncesi kod
        if class_starts[0][0] > 0:
            pre_class = code[:class_starts[0][0]].rstrip()
            if pre_class.strip():
                chunks.append(self._make_chunk(pre_class, file_path, 0, "imports", "Import ve üst seviye kod"))

        for idx, (start, class_name) in enumerate(class_starts):
            end = class_starts[idx + 1][0] if idx + 1 < len(class_starts) else len(code)
            class_code = code[start:end].rstrip()
            
            if len(class_code) > max_chars:
                # Büyük sınıfı method bazında böl
                method_chunks = self._split_class_methods(class_code, class_name, file_path, max_chars)
                for mc in method_chunks:
                    mc.sequence = len(chunks)
                    chunks.append(mc)
            else:
                chunks.append(self._make_chunk(class_code, file_path, len(chunks), "class", class_name))

        return chunks

    def _split_class_methods(
        self, class_code: str, class_name: str, file_path: str, max_chars: int
    ) -> List[CodeChunk]:
        """Bir sınıfı method'larına göre böler."""
        chunks: List[CodeChunk] = []
        lines = class_code.splitlines(keepends=True)
        
        # Sınıf header'ını bul (class tanımı + __init__ öncesi)
        header_lines = []
        body_start = 0
        in_header = True
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            if in_header:
                header_lines.append(line)
                if stripped.startswith("def ") or stripped.startswith("async def "):
                    in_header = False
                    body_start = i
                    header_lines.pop()  # def satırını header'dan çıkar
                    break
        
        if header_lines:
            header = "".join(header_lines)
            if header.strip():
                chunks.append(self._make_chunk(header, file_path, 0, "class_header", f"{class_name} header"))

        # Method'ları bul (indented def)
        method_pattern = re.compile(r"^(\s+)((?:@\w+.*\n)*\s+(?:def|async\s+def)\s+\w+)", re.MULTILINE)
        method_starts = [
            (m.start(), m.group(2).split("def ")[-1].split("(")[0].strip())
            for m in method_pattern.finditer(class_code)
        ]

        if not method_starts:
            # Method bulunamadı, satır bazında böl
            return self._split_by_lines(class_code, file_path, max_chars)

        for idx, (start, method_name) in enumerate(method_starts):
            end = method_starts[idx + 1][0] if idx + 1 < len(method_starts) else len(class_code)
            method_code = class_code[start:end].rstrip()
            
            if len(method_code) > max_chars:
                sub_chunks = self._split_by_lines(method_code, file_path, max_chars)
                for sc in sub_chunks:
                    sc.label = f"{class_name}.{method_name} (parça {sc.sequence + 1})"
                    sc.sequence = len(chunks)
                    chunks.append(sc)
            else:
                chunks.append(self._make_chunk(
                    method_code, file_path, len(chunks), "method", f"{class_name}.{method_name}"
                ))

        return chunks

    def _split_by_blocks(self, code: str, file_path: str, max_chars: int) -> List[CodeChunk]:
        """Mantıksal bloklar bazında böler (boş satır grupları ile ayrılmış)."""
        chunks: List[CodeChunk] = []
        
        # Çift boş satırlarla bloklara ayır
        blocks = re.split(r"\n\n\n+", code)
        
        current_block = ""
        current_label = "blok"
        
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            
            # Blok etiketini belirle
            first_line = block.splitlines()[0].strip() if block.splitlines() else ""
            if first_line.startswith("import ") or first_line.startswith("from "):
                label = "imports"
            elif first_line.startswith("class "):
                label = first_line.split("(")[0].split(":")[0]
            elif first_line.startswith("def ") or first_line.startswith("async def "):
                label = first_line.split("(")[0]
            elif first_line.startswith("#"):
                label = first_line.lstrip("# ").strip()[:40]
            elif first_line.startswith("@app."):
                label = f"route: {first_line}"
            else:
                label = first_line[:40]
            
            test_merge = f"{current_block}\n\n{block}" if current_block else block
            
            if len(test_merge) <= max_chars:
                current_block = test_merge
                current_label = label if not current_block.strip() else current_label
            else:
                if current_block.strip():
                    chunks.append(self._make_chunk(
                        current_block, file_path, len(chunks), "block", current_label
                    ))
                
                if len(block) > max_chars:
                    sub_chunks = self._split_by_lines(block, file_path, max_chars)
                    for sc in sub_chunks:
                        sc.sequence = len(chunks)
                        chunks.append(sc)
                    current_block = ""
                else:
                    current_block = block
                    current_label = label
        
        if current_block.strip():
            chunks.append(self._make_chunk(
                current_block, file_path, len(chunks), "block", current_label
            ))
        
        return chunks or [self._make_chunk(code, file_path, 0, "complete", "Tam dosya")]

    def _split_by_lines(self, code: str, file_path: str, max_chars: int) -> List[CodeChunk]:
        """Son çare: sabit satır sayısına göre böler."""
        chunks: List[CodeChunk] = []
        lines = code.splitlines(keepends=True)
        current_lines: List[str] = []
        current_chars = 0

        for line in lines:
            if current_chars + len(line) > max_chars and current_lines:
                content = "".join(current_lines)
                chunks.append(self._make_chunk(
                    content, file_path, len(chunks), "segment",
                    f"Satır {sum(len(c.content.splitlines()) for c in chunks) + 1}-{sum(len(c.content.splitlines()) for c in chunks) + len(current_lines)}"
                ))
                current_lines = []
                current_chars = 0
            
            current_lines.append(line)
            current_chars += len(line)
        
        if current_lines:
            content = "".join(current_lines)
            chunks.append(self._make_chunk(
                content, file_path, len(chunks), "segment",
                f"Satır {sum(len(c.content.splitlines()) for c in chunks) + 1}-son"
            ))

        return chunks

    def _merge_tiny_chunks(self, chunks: List[CodeChunk], max_chars: int) -> List[CodeChunk]:
        """Çok küçük chunk'ları önceki chunk ile birleştirir."""
        if len(chunks) <= 1:
            return chunks

        merged: List[CodeChunk] = [chunks[0]]
        for chunk in chunks[1:]:
            prev = merged[-1]
            combined_len = len(prev.content) + len(chunk.content) + 2  # +2 for \n\n
            
            if chunk.char_count < self.MIN_CHUNK_CHARS and combined_len <= max_chars:
                prev.content = f"{prev.content}\n\n{chunk.content}"
                prev.char_count = len(prev.content)
                prev.line_count = len(prev.content.splitlines())
                prev.label = f"{prev.label} + {chunk.label}"
            else:
                chunk.sequence = len(merged)
                merged.append(chunk)

        return merged

    # ── Vault Entegrasyonu ────────────────────────────────────

    def write_chunks_to_vault(
        self,
        code: str,
        file_path: str,
        tags: Optional[List[str]] = None,
        strategy: str = ChunkStrategy.AUTO,
    ) -> List[VaultNote]:
        """
        Kodu chunk'lara böler ve vault'a yazar.
        Her chunk bir VaultNote olarak saklanır.
        
        Returns:
            Oluşturulan VaultNote listesi
        """
        chunks = self.split_code(code, file_path, strategy)
        notes: List[VaultNote] = []
        
        parent_id = re.sub(r"[^a-zA-Z0-9_-]", "_", file_path)[:60]
        ext = os.path.splitext(file_path)[1].lower()
        language_map = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".html": "html", ".css": "css", ".json": "json",
        }
        language = language_map.get(ext, ext.lstrip("."))

        # Chunk bağlantılarını oluştur
        chunk_ids = [c.chunk_id for c in chunks]

        for i, chunk in enumerate(chunks):
            links = []
            if i > 0:
                links.append(chunk_ids[i - 1])
            if i < len(chunks) - 1:
                links.append(chunk_ids[i + 1])

            all_tags = list(set(
                (tags or []) + 
                [language, "chunk", chunk.chunk_type, f"file:{os.path.basename(file_path)}"]
            ))

            note = self.vault.create_note(
                title=f"{os.path.basename(file_path)} [{chunk.label}]",
                content=chunk.content,
                note_type="chunk",
                tags=all_tags,
                links=links,
                metadata={
                    "parent_file": file_path,
                    "parent_id": parent_id,
                    "chunk_sequence": chunk.sequence,
                    "chunk_type": chunk.chunk_type,
                    "chunk_label": chunk.label,
                    "language": language,
                    "total_chunks": len(chunks),
                },
                note_id=chunk.chunk_id,
            )
            notes.append(note)

        # Ana dosya referans notu oluştur
        manifest_content = json.dumps({
            "file_path": file_path,
            "total_chunks": len(chunks),
            "total_chars": sum(c.char_count for c in chunks),
            "total_lines": sum(c.line_count for c in chunks),
            "strategy": strategy,
            "chunk_order": [c.chunk_id for c in chunks],
            "chunk_details": [c.to_dict() for c in chunks],
        }, ensure_ascii=False, indent=2)

        self.vault.create_note(
            title=f"[MANIFEST] {os.path.basename(file_path)} [MANIFEST]",
            content=manifest_content,
            note_type="doc",
            tags=(tags or []) + ["manifest", language],
            links=chunk_ids,
            metadata={
                "parent_file": file_path,
                "is_manifest": True,
                "chunk_count": len(chunks),
            },
            note_id=f"{parent_id}_manifest",
        )

        print(f"[CHUNK_WRITER] {file_path} -> {len(chunks)} chunk yazildi (toplam {sum(c.char_count for c in chunks):,} karakter)")
        return notes

    def get_chunk_for_generation(
        self,
        file_path: str,
        chunk_index: int,
    ) -> Optional[Dict[str, Any]]:
        """
        AI'ın üretmesi gereken bir chunk'ın bağlamını döndürür.
        Önceki ve sonraki chunk'ların özetlerini içerir.
        """
        parent_id = re.sub(r"[^a-zA-Z0-9_-]", "_", file_path)[:60]
        manifest_id = f"{parent_id}_manifest"
        manifest_note = self.vault.get_note(manifest_id)
        
        if manifest_note is None:
            return None

        try:
            manifest = json.loads(manifest_note.content)
        except json.JSONDecodeError:
            return None

        chunk_order = manifest.get("chunk_order", [])
        if chunk_index < 0 or chunk_index >= len(chunk_order):
            return None

        target_id = chunk_order[chunk_index]
        target_note = self.vault.get_note(target_id)

        # Bağlam: önceki chunk'ın son 5 satırı ve sonraki chunk'ın ilk 5 satırı
        prev_context = ""
        next_context = ""
        
        if chunk_index > 0:
            prev_note = self.vault.get_note(chunk_order[chunk_index - 1])
            if prev_note:
                prev_lines = prev_note.content.splitlines()
                prev_context = "\n".join(prev_lines[-5:])
        
        if chunk_index < len(chunk_order) - 1:
            next_note = self.vault.get_note(chunk_order[chunk_index + 1])
            if next_note:
                next_lines = next_note.content.splitlines()
                next_context = "\n".join(next_lines[:5])

        return {
            "file_path": file_path,
            "chunk_index": chunk_index,
            "total_chunks": len(chunk_order),
            "chunk_id": target_id,
            "current_content": target_note.content if target_note else "",
            "chunk_type": manifest["chunk_details"][chunk_index]["chunk_type"],
            "chunk_label": manifest["chunk_details"][chunk_index]["label"],
            "previous_context": prev_context,
            "next_context": next_context,
            "max_chars": self.MAX_CHUNK_CHARS,
        }

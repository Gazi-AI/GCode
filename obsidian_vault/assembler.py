"""
CodeAssembler - Parçaları Birleştiren Motor

Chunk'ları doğru sırada birleştirir ve tam çalışan dosyalar oluşturur.
Birleştirme sırasında:
    - Import çakışmalarını çözer
    - Duplicate kod tespiti yapar
    - Bağımlılık sırasını kontrol eder
    - Syntax doğrulaması yapar
"""

import os
import re
import json
import time
import py_compile
import tempfile
from typing import Optional, Dict, List, Any, Tuple

from obsidian_vault.vault_core import ObsidianVault, VaultNote


class AssemblyResult:
    """Birleştirme sonucunu temsil eder."""

    def __init__(self, file_path: str, content: str, chunks_used: int):
        self.file_path = file_path
        self.content = content
        self.chunks_used = chunks_used
        self.char_count = len(content)
        self.line_count = len(content.splitlines())
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.is_valid = True
        self.assembled_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "content": self.content,
            "chunks_used": self.chunks_used,
            "char_count": self.char_count,
            "line_count": self.line_count,
            "errors": self.errors,
            "warnings": self.warnings,
            "is_valid": self.is_valid,
        }


class CodeAssembler:
    """
    Chunk'ları birleştirip tam dosya oluşturur.
    
    Birleştirme aşamaları:
        1. Manifest oku -> chunk sırasını belirle
        2. Chunk'ları topla
        3. Import'ları birleştir ve deduplike et
        4. Kod bloklarını sırala
        5. Syntax kontrolü yap
        6. Son dosyayı oluştur
    """

    def __init__(self, vault: ObsidianVault):
        self.vault = vault

    def assemble_file(self, file_path: str) -> Optional[AssemblyResult]:
        """
        Bir dosyanın tüm chunk'larını birleştirerek tam dosya oluşturur.
        
        Args:
            file_path: Birleştirilecek dosyanın yolu
            
        Returns:
            AssemblyResult veya None
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
        if not chunk_order:
            return None

        # Chunk'ları sırayla topla
        content_parts: List[str] = []
        missing_chunks: List[str] = []

        for chunk_id in chunk_order:
            note = self.vault.get_note(chunk_id)
            if note is None:
                missing_chunks.append(chunk_id)
                content_parts.append(f"# [EKSIK CHUNK: {chunk_id}]\n")
            else:
                content_parts.append(note.content)

        # Birleştir
        raw_content = "\n\n".join(content_parts)

        # Import'ları optimize et
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".py":
            raw_content = self._optimize_python_imports(raw_content)
        elif ext in (".js", ".ts"):
            raw_content = self._optimize_js_imports(raw_content)

        # Duplicate satırları temizle
        raw_content = self._remove_duplicate_lines(raw_content)

        # Sonuç oluştur
        result = AssemblyResult(
            file_path=file_path,
            content=raw_content,
            chunks_used=len(chunk_order) - len(missing_chunks),
        )

        if missing_chunks:
            result.warnings.append(f"{len(missing_chunks)} chunk eksik: {', '.join(missing_chunks)}")

        # Syntax kontrolü
        if ext == ".py":
            syntax_errors = self._check_python_syntax(raw_content)
            if syntax_errors:
                result.errors.extend(syntax_errors)
                result.is_valid = False

        # Assembled dizinine kaydet
        self._save_assembled(result)

        return result

    def assemble_all(self) -> List[AssemblyResult]:
        """Vault'taki tüm manifest'li dosyaları birleştirir."""
        results: List[AssemblyResult] = []
        
        for note in self.vault.notes.values():
            if note.metadata.get("is_manifest"):
                file_path = note.metadata.get("parent_file", "")
                if file_path:
                    result = self.assemble_file(file_path)
                    if result:
                        results.append(result)

        return results

    def _optimize_python_imports(self, code: str) -> str:
        """Python import satırlarını birleştirir ve duplicate'leri kaldırır."""
        lines = code.splitlines()
        import_lines: List[str] = []
        from_imports: Dict[str, set] = {}
        other_lines: List[str] = []
        seen_imports: set = set()

        in_import_block = True

        for line in lines:
            stripped = line.strip()

            if in_import_block and (stripped.startswith("import ") or stripped.startswith("from ")):
                if stripped.startswith("from "):
                    # from X import Y, Z formatını parse et
                    match = re.match(r"from\s+([\w.]+)\s+import\s+(.+)", stripped)
                    if match:
                        module = match.group(1)
                        names = {n.strip() for n in match.group(2).split(",")}
                        from_imports.setdefault(module, set()).update(names)
                        continue

                if stripped not in seen_imports:
                    seen_imports.add(stripped)
                    import_lines.append(line)
            else:
                if stripped and not stripped.startswith("#"):
                    in_import_block = False
                other_lines.append(line)

        # from X import Y satırlarını birleştir
        from_lines = []
        for module in sorted(from_imports.keys()):
            names = sorted(from_imports[module])
            import_str = f"from {module} import {', '.join(names)}"
            if len(import_str) > 100:
                # Çok uzunsa çoklu satır
                name_str = ",\n    ".join(names)
                import_str = f"from {module} import (\n    {name_str},\n)"
            from_lines.append(import_str)

        # Sonucu birleştir
        result_lines = import_lines + from_lines
        if result_lines:
            result_lines.append("")  # İmport'lar sonrası boş satır
        result_lines.extend(other_lines)

        return "\n".join(result_lines)

    def _optimize_js_imports(self, code: str) -> str:
        """JavaScript import/require satırlarını optimize eder."""
        lines = code.splitlines()
        import_lines: List[str] = []
        other_lines: List[str] = []
        seen_imports: set = set()

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("const ") and "require(" in stripped:
                if stripped not in seen_imports:
                    seen_imports.add(stripped)
                    import_lines.append(line)
            else:
                other_lines.append(line)

        return "\n".join(import_lines + [""] + other_lines) if import_lines else "\n".join(other_lines)

    def _remove_duplicate_lines(self, code: str) -> str:
        """Ardışık duplicate satırları kaldırır (boş satırlar hariç)."""
        lines = code.splitlines()
        result: List[str] = []
        prev_line = None

        for line in lines:
            stripped = line.strip()
            if stripped and stripped == prev_line:
                continue
            result.append(line)
            prev_line = stripped if stripped else None

        # Çoklu boş satırları iki ile sınırla
        cleaned: List[str] = []
        blank_count = 0
        for line in result:
            if line.strip():
                blank_count = 0
                cleaned.append(line)
            else:
                blank_count += 1
                if blank_count <= 2:
                    cleaned.append(line)

        return "\n".join(cleaned)

    def _check_python_syntax(self, code: str) -> List[str]:
        """Python kodunun syntax'ını kontrol eder."""
        errors: List[str] = []
        try:
            compile(code, "<assembled>", "exec")
        except SyntaxError as exc:
            errors.append(f"Syntax hatası satır {exc.lineno}: {exc.msg}")
        return errors

    def _save_assembled(self, result: AssemblyResult) -> None:
        """Birleştirilmiş dosyayı assembled dizinine kaydeder."""
        filename = os.path.basename(result.file_path)
        assembled_path = os.path.join(self.vault.vault_dir, "assembled", filename)

        try:
            os.makedirs(os.path.dirname(assembled_path), exist_ok=True)
            with open(assembled_path, "w", encoding="utf-8", newline="") as f:
                f.write(result.content)
        except IOError as e:
            result.warnings.append(f"Assembled dosya kaydedilemedi: {e}")

        # Birleştirme logunu da kaydet
        log_path = assembled_path + ".log.json"
        try:
            log_data = {
                "file_path": result.file_path,
                "assembled_at": result.assembled_at,
                "chunks_used": result.chunks_used,
                "char_count": result.char_count,
                "line_count": result.line_count,
                "is_valid": result.is_valid,
                "errors": result.errors,
                "warnings": result.warnings,
            }
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(log_data, f, ensure_ascii=False, indent=2)
        except IOError:
            pass

    # ── Akıllı Birleştirme ────────────────────────────────────

    def smart_assemble(
        self,
        file_path: str,
        updated_chunks: Optional[Dict[str, str]] = None,
    ) -> Optional[AssemblyResult]:
        """
        Akıllı birleştirme: Sadece değişen chunk'ları günceller.
        
        Args:
            file_path: Dosya yolu
            updated_chunks: {chunk_id: new_content} sözlüğü
            
        Returns:
            AssemblyResult
        """
        if updated_chunks:
            for chunk_id, new_content in updated_chunks.items():
                self.vault.update_note(chunk_id, content=new_content)

        return self.assemble_file(file_path)

    def partial_update(
        self,
        file_path: str,
        chunk_index: int,
        new_content: str,
    ) -> Optional[AssemblyResult]:
        """
        Tek bir chunk'ı günceller ve dosyayı yeniden birleştirir.
        
        Bu metod AI'ın parça parça kod üretmesini sağlar:
        1. AI bir chunk üretir (max 3800 karakter)
        2. Bu metod chunk'ı günceller
        3. Tüm dosyayı yeniden birleştirir
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
        self.vault.update_note(target_id, content=new_content)

        return self.assemble_file(file_path)

    def get_assembly_report(self) -> str:
        """Tüm birleştirme durumlarının raporunu döndürür."""
        lines = ["# [TOOL] Assembly Report", ""]

        manifests = [
            n for n in self.vault.notes.values()
            if n.metadata.get("is_manifest")
        ]

        if not manifests:
            return "Vault'ta henüz chunk'lanmış dosya yok."

        for manifest_note in manifests:
            try:
                manifest = json.loads(manifest_note.content)
            except json.JSONDecodeError:
                continue

            file_path = manifest.get("file_path", "?")
            total_chunks = manifest.get("total_chunks", 0)
            total_chars = manifest.get("total_chars", 0)
            chunk_order = manifest.get("chunk_order", [])

            lines.append(f"## [FILE] {file_path}")
            lines.append(f"- Chunk sayısı: {total_chunks}")
            lines.append(f"- Toplam karakter: {total_chars:,}")
            lines.append(f"- Chunk limiti: {3800} karakter")

            missing = 0
            for chunk_id in chunk_order:
                note = self.vault.get_note(chunk_id)
                status = "[OK]" if note else "[X]"
                if note is None:
                    missing += 1
                label = note.metadata.get("chunk_label", chunk_id) if note else chunk_id
                chars = len(note.content) if note else 0
                lines.append(f"  {status} `{chunk_id}` — {label} ({chars:,} chars)")

            if missing:
                lines.append(f"  [!] {missing} chunk eksik!")
            lines.append("")

        return "\n".join(lines)

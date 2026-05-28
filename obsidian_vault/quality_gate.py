"""
QualityGate - Parça Kalite Kontrolü

Her chunk'ın ve birleştirilmiş dosyanın kalitesini kontrol eder.
Syntax, bağımlılık, tutarlılık ve completeness kontrolleri yapar.
"""

import os
import re
import json
import py_compile
import tempfile
from typing import Optional, Dict, List, Any, Tuple

from obsidian_vault.vault_core import ObsidianVault, VaultNote


class QualityIssue:
    """Bir kalite sorununu temsil eder."""

    SEVERITY_ERROR = "error"
    SEVERITY_WARNING = "warning"
    SEVERITY_INFO = "info"

    def __init__(
        self,
        message: str,
        severity: str = "error",
        file_path: str = "",
        chunk_id: str = "",
        line: int = 0,
        rule: str = "",
    ):
        self.message = message
        self.severity = severity
        self.file_path = file_path
        self.chunk_id = chunk_id
        self.line = line
        self.rule = rule

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message": self.message,
            "severity": self.severity,
            "file_path": self.file_path,
            "chunk_id": self.chunk_id,
            "line": self.line,
            "rule": self.rule,
        }

    def __str__(self):
        prefix = {"error": "[X]", "warning": "[!]", "info": "[i]"}.get(self.severity, "•")
        loc = f" ({self.file_path}:{self.line})" if self.line else f" ({self.file_path})" if self.file_path else ""
        return f"{prefix} [{self.rule}]{loc} {self.message}"


class QualityResult:
    """Kalite kontrolü sonucu."""

    def __init__(self):
        self.issues: List[QualityIssue] = []
        self.passed = True
        self.score = 100.0
        self.checks_run = 0

    def add_issue(self, issue: QualityIssue) -> None:
        self.issues.append(issue)
        if issue.severity == QualityIssue.SEVERITY_ERROR:
            self.passed = False
            self.score -= 15.0
        elif issue.severity == QualityIssue.SEVERITY_WARNING:
            self.score -= 5.0
        else:
            self.score -= 1.0
        self.score = max(0.0, self.score)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == QualityIssue.SEVERITY_ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == QualityIssue.SEVERITY_WARNING)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "score": round(self.score, 1),
            "checks_run": self.checks_run,
            "errors": self.error_count,
            "warnings": self.warning_count,
            "issues": [i.to_dict() for i in self.issues],
        }

    def summary(self) -> str:
        status = "[OK] PASSED" if self.passed else "[X] FAILED"
        return (
            f"{status} — Skor: {self.score:.0f}/100 | "
            f"Hatalar: {self.error_count} | Uyarılar: {self.warning_count} | "
            f"Kontroller: {self.checks_run}"
        )


class QualityGate:
    """
    Kod kalite kontrol kapısı.
    
    Kontroller:
        1. Syntax doğrulama (Python compile, HTML tag matching)
        2. Placeholder tespiti (TODO, pass, lorem ipsum)
        3. Import tutarlılığı
        4. Chunk boyut kontrolü
        5. Encoding kontrolü
        6. Bağımlılık kontrolü (cross-chunk)
        7. Completeness kontrolü
    """

    # Placeholder pattern'leri
    PLACEHOLDER_PATTERNS = [
        r"\bTODO\b",
        r"\bFIXME\b",
        r"\bHACK\b",
        r"\bXXX\b",
        r"\bimplement\s+later\b",
        r"\byour\s+code\s+here\b",
        r"\bpass\s*$",  # Sadece pass olan satır
        r"\blorem\s+ipsum\b",
        r"\bplaceholder\b",
        r"\.\.\.\s*$",  # Sadece ... olan satır
    ]

    MAX_CHUNK_CHARS = 3800

    def __init__(self, vault: ObsidianVault):
        self.vault = vault

    # ── Tek Chunk Kontrolü ────────────────────────────────────

    def check_chunk(
        self,
        content: str,
        file_path: str,
        chunk_id: str = "",
        chunk_type: str = "body",
    ) -> QualityResult:
        """Tek bir chunk'ın kalitesini kontrol eder."""
        result = QualityResult()
        ext = os.path.splitext(file_path)[1].lower()

        # 1. Boşluk kontrolü
        result.checks_run += 1
        if not content or not content.strip():
            result.add_issue(QualityIssue(
                "Chunk içeriği boş", "error", file_path, chunk_id, rule="empty_content"
            ))
            return result

        # 2. Boyut kontrolü
        result.checks_run += 1
        if len(content) > self.MAX_CHUNK_CHARS:
            result.add_issue(QualityIssue(
                f"Chunk çok büyük: {len(content)} > {self.MAX_CHUNK_CHARS} karakter",
                "error", file_path, chunk_id, rule="chunk_size"
            ))

        # 3. Minimum boyut
        result.checks_run += 1
        if len(content.strip()) < 20 and chunk_type not in ("imports", "config"):
            result.add_issue(QualityIssue(
                f"Chunk çok kısa: {len(content.strip())} karakter",
                "warning", file_path, chunk_id, rule="min_size"
            ))

        # 4. Placeholder kontrolü
        result.checks_run += 1
        for pattern in self.PLACEHOLDER_PATTERNS:
            matches = re.findall(pattern, content, re.MULTILINE | re.IGNORECASE)
            if matches:
                # "pass" kontrolünde context'e bak — except/class body'de normal
                if "pass" in pattern.lower():
                    pass_lines = [
                        i for i, line in enumerate(content.splitlines(), 1)
                        if re.match(r"^\s*pass\s*$", line)
                    ]
                    for line_num in pass_lines:
                        lines = content.splitlines()
                        if line_num >= 2:
                            prev = lines[line_num - 2].strip()
                            if prev.endswith(":") and any(
                                kw in prev for kw in ("except", "finally", "else", "elif")
                            ):
                                continue
                        result.add_issue(QualityIssue(
                            f"Placeholder 'pass' bulundu satır {line_num}",
                            "warning", file_path, chunk_id, line=line_num, rule="placeholder"
                        ))
                else:
                    result.add_issue(QualityIssue(
                        f"Placeholder pattern bulundu: {matches[0]}",
                        "warning", file_path, chunk_id, rule="placeholder"
                    ))

        # 5. Python syntax kontrolü
        if ext == ".py":
            result.checks_run += 1
            try:
                compile(content, f"<chunk:{chunk_id}>", "exec")
            except SyntaxError as exc:
                # Chunk'lar tek başlarına compile edilemeyebilir (class body gibi)
                # Sadece ciddi hataları raporla
                if chunk_type in ("complete", "imports", "footer"):
                    result.add_issue(QualityIssue(
                        f"Python syntax hatası: {exc.msg} (satır {exc.lineno})",
                        "error", file_path, chunk_id, line=exc.lineno or 0, rule="python_syntax"
                    ))
                else:
                    result.add_issue(QualityIssue(
                        f"Python syntax uyarısı (chunk bağlamında normal olabilir): {exc.msg}",
                        "info", file_path, chunk_id, rule="python_syntax_partial"
                    ))

        # 6. HTML tag kontrolü
        if ext in (".html", ".htm"):
            result.checks_run += 1
            open_tags = re.findall(r"<(\w+)(?:\s[^>]*)?>", content)
            close_tags = re.findall(r"</(\w+)>", content)
            void_tags = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr"}
            open_non_void = [t.lower() for t in open_tags if t.lower() not in void_tags]
            close_lower = [t.lower() for t in close_tags]
            
            if chunk_type == "complete":
                for tag in open_non_void:
                    if tag not in close_lower:
                        result.add_issue(QualityIssue(
                            f"Kapatılmamış HTML tag: <{tag}>",
                            "warning", file_path, chunk_id, rule="html_tag_mismatch"
                        ))

        # 7. Encoding kontrolü
        result.checks_run += 1
        try:
            content.encode("utf-8")
        except UnicodeEncodeError:
            result.add_issue(QualityIssue(
                "UTF-8 encoding hatası",
                "error", file_path, chunk_id, rule="encoding"
            ))

        return result

    # ── Tam Dosya Kontrolü ────────────────────────────────────

    def check_assembled_file(
        self,
        content: str,
        file_path: str,
        plan: Optional[Dict[str, Any]] = None,
    ) -> QualityResult:
        """Birleştirilmiş tam dosyanın kalitesini kontrol eder."""
        result = QualityResult()
        ext = os.path.splitext(file_path)[1].lower()

        # Temel chunk kontrollerini çalıştır
        chunk_result = self.check_chunk(content, file_path, "assembled", "complete")
        for issue in chunk_result.issues:
            result.add_issue(issue)
        result.checks_run += chunk_result.checks_run

        # Python'a özel ek kontroller
        if ext == ".py":
            self._check_python_file(result, content, file_path, plan)

        # HTML'e özel kontroller
        if ext in (".html", ".htm"):
            self._check_html_file(result, content, file_path)

        # CSS'e özel kontroller
        if ext == ".css":
            self._check_css_file(result, content, file_path)

        # JS'e özel kontroller
        if ext in (".js", ".ts"):
            self._check_js_file(result, content, file_path)

        return result

    def _check_python_file(
        self,
        result: QualityResult,
        content: str,
        file_path: str,
        plan: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Python dosyasına özel kalite kontrolleri."""
        basename = os.path.basename(file_path).lower()
        lowered = content.lower()

        # Import kontrolü
        result.checks_run += 1
        imports = set()
        for match in re.finditer(r"^import\s+(\w+)", content, re.MULTILINE):
            imports.add(match.group(1))
        for match in re.finditer(r"^from\s+(\w+)", content, re.MULTILINE):
            imports.add(match.group(1))
        
        # Kullanılmayan import tespiti (basit)
        for imp in imports:
            if imp in ("os", "sys", "json", "re", "time", "typing", "pathlib"):
                continue
            # İmport edilen modülün kodda kullanılıp kullanılmadığını kontrol et
            import_pattern = re.compile(rf"\b{re.escape(imp)}\b")
            # İmport satırları hariç say
            non_import_lines = [l for l in content.splitlines() 
                               if not l.strip().startswith(("import ", "from "))]
            usage_count = sum(1 for l in non_import_lines if import_pattern.search(l))
            if usage_count == 0:
                result.add_issue(QualityIssue(
                    f"Kullanılmayan import olabilir: {imp}",
                    "info", file_path, rule="unused_import"
                ))

        # Server dosyası kontrolleri
        if basename in ("app.py", "main.py", "server.py"):
            result.checks_run += 1
            if len(content.strip()) < 500:
                result.add_issue(QualityIssue(
                    "Server dosyası ciddi uygulama için çok kısa",
                    "warning", file_path, rule="server_too_short"
                ))
            
            result.checks_run += 1
            has_flask = "flask" in lowered
            has_fastapi = "fastapi" in lowered
            if has_flask and ".run(" not in lowered:
                result.add_issue(QualityIssue(
                    "Flask app.run() entrypoint eksik",
                    "error", file_path, rule="missing_entrypoint"
                ))
            if has_fastapi and "uvicorn.run" not in lowered:
                result.add_issue(QualityIssue(
                    "FastAPI uvicorn.run() entrypoint eksik",
                    "error", file_path, rule="missing_entrypoint"
                ))

        # Duplicate fonksiyon tespiti
        result.checks_run += 1
        func_defs = re.findall(r"^(?:def|async\s+def)\s+(\w+)\s*\(", content, re.MULTILINE)
        from collections import Counter
        func_counts = Counter(func_defs)
        for func_name, count in func_counts.items():
            if count > 1:
                result.add_issue(QualityIssue(
                    f"Duplicate fonksiyon tanımı: {func_name} ({count} kez)",
                    "error", file_path, rule="duplicate_function"
                ))

    def _check_html_file(self, result: QualityResult, content: str, file_path: str) -> None:
        """HTML dosyasına özel kontroller."""
        lowered = content.lower()

        result.checks_run += 1
        if "<!doctype" not in lowered:
            result.add_issue(QualityIssue(
                "DOCTYPE tanımı eksik",
                "warning", file_path, rule="missing_doctype"
            ))

        result.checks_run += 1
        if "<html" not in lowered or "</html>" not in lowered:
            result.add_issue(QualityIssue(
                "HTML root element eksik veya kapatılmamış",
                "error", file_path, rule="missing_html_root"
            ))

        result.checks_run += 1
        if '<meta charset' not in lowered and '<meta http-equiv="content-type"' not in lowered:
            result.add_issue(QualityIssue(
                "Charset meta tag'i eksik",
                "warning", file_path, rule="missing_charset"
            ))

        result.checks_run += 1
        if '<meta name="viewport"' not in lowered:
            result.add_issue(QualityIssue(
                "Viewport meta tag'i eksik",
                "warning", file_path, rule="missing_viewport"
            ))

    def _check_css_file(self, result: QualityResult, content: str, file_path: str) -> None:
        """CSS dosyasına özel kontroller."""
        result.checks_run += 1
        if len(content.strip()) < 100:
            result.add_issue(QualityIssue(
                "CSS dosyası çok kısa",
                "warning", file_path, rule="css_too_short"
            ))

        result.checks_run += 1
        open_braces = content.count("{")
        close_braces = content.count("}")
        if open_braces != close_braces:
            result.add_issue(QualityIssue(
                f"CSS süslü parantez uyuşmazlığı: {open_braces} açık, {close_braces} kapalı",
                "error", file_path, rule="css_brace_mismatch"
            ))

    def _check_js_file(self, result: QualityResult, content: str, file_path: str) -> None:
        """JavaScript dosyasına özel kontroller."""
        result.checks_run += 1
        if "console.log" in content:
            count = content.count("console.log")
            if count > 5:
                result.add_issue(QualityIssue(
                    f"Çok fazla console.log ({count} adet) — temizlenmeli",
                    "info", file_path, rule="excessive_logging"
                ))

    # ── Toplu Kontrol ─────────────────────────────────────────

    def check_all_chunks(self, file_path: str) -> QualityResult:
        """Bir dosyanın tüm chunk'larını kontrol eder."""
        result = QualityResult()
        
        parent_id = re.sub(r"[^a-zA-Z0-9_-]", "_", file_path)[:60]
        manifest_id = f"{parent_id}_manifest"
        manifest_note = self.vault.get_note(manifest_id)

        if manifest_note is None:
            result.add_issue(QualityIssue(
                "Manifest bulunamadı",
                "error", file_path, rule="missing_manifest"
            ))
            return result

        try:
            manifest = json.loads(manifest_note.content)
        except json.JSONDecodeError:
            result.add_issue(QualityIssue(
                "Manifest JSON parse edilemedi",
                "error", file_path, rule="invalid_manifest"
            ))
            return result

        chunk_order = manifest.get("chunk_order", [])
        
        for chunk_id in chunk_order:
            note = self.vault.get_note(chunk_id)
            if note is None:
                result.add_issue(QualityIssue(
                    f"Chunk bulunamadı: {chunk_id}",
                    "error", file_path, chunk_id, rule="missing_chunk"
                ))
                continue
            
            chunk_result = self.check_chunk(
                note.content,
                file_path,
                chunk_id,
                note.metadata.get("chunk_type", "body"),
            )
            for issue in chunk_result.issues:
                result.add_issue(issue)
            result.checks_run += chunk_result.checks_run

        return result

    def full_quality_report(self) -> str:
        """Tüm vault'un kalite raporunu oluşturur."""
        lines = ["# [QUALITY] Quality Gate Report", ""]

        manifests = [
            n for n in self.vault.notes.values()
            if n.metadata.get("is_manifest")
        ]

        if not manifests:
            return "Vault'ta kontrol edilecek dosya yok."

        total_errors = 0
        total_warnings = 0

        for manifest_note in manifests:
            try:
                manifest = json.loads(manifest_note.content)
            except json.JSONDecodeError:
                continue

            file_path = manifest.get("file_path", "?")
            result = self.check_all_chunks(file_path)

            total_errors += result.error_count
            total_warnings += result.warning_count

            status = "[OK]" if result.passed else "[X]"
            lines.append(f"## {status} {file_path}")
            lines.append(f"Skor: {result.score:.0f}/100 | Hatalar: {result.error_count} | Uyarılar: {result.warning_count}")
            
            for issue in result.issues[:10]:
                lines.append(f"  {issue}")
            lines.append("")

        lines.insert(1, f"**Toplam:** {total_errors} hata, {total_warnings} uyarı")
        lines.insert(2, "")

        return "\n".join(lines)

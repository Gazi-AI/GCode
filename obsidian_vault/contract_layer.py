"""
GCode Contract Layer — Obsidian Vault Kontrat Katmanı

Bir ajan bir dosyada fonksiyon yazdığında, bu fonksiyonun imzasını/
kontratını (adı, parametreleri, dönüş tipi, URL yapısı) otomatik olarak
çıkarır ve Obsidian Vault'a mikro-not olarak kaydeder.

Diğer ajanlar kod yazarken koca dosyayı değil, sadece bu kontratı okur.
Böylece backend ve frontend birbirine %100 uyumlu olur.

Desteklenen Kontrat Türleri:
    - Python fonksiyonları (AST ile)
    - Flask/FastAPI route'ları
    - JavaScript fonksiyon/class tanımları (regex ile)
    - HTML element ID/class haritası
    - CSS class listesi
    - API response key'leri
"""

import ast
import os
import re
import json
import time
import hashlib
import logging
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger("GCode.ContractLayer")


# ── Kontrat Veri Yapıları ─────────────────────────────────────

@dataclass
class FunctionContract:
    """Tek bir fonksiyonun kontratı."""
    file_path: str
    name: str
    params: List[Dict[str, str]]    # [{"name": "x", "type": "int", "default": "0"}]
    returns: str                     # Dönüş tipi
    decorators: List[str]            # ["@app.route('/api/chat')"]
    docstring: str                   # İlk satır
    dependencies: List[str]          # ["agent.py::GaziAgent.chat"]
    line_number: int = 0
    is_method: bool = False          # Sınıf metodu mu?
    class_name: str = ""             # Eğer metot ise ait olduğu sınıf

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file": self.file_path,
            "name": self.name,
            "full_name": f"{self.class_name}.{self.name}" if self.class_name else self.name,
            "params": self.params,
            "returns": self.returns,
            "decorators": self.decorators,
            "docstring": self.docstring,
            "dependencies": self.dependencies,
            "line": self.line_number,
            "is_method": self.is_method,
            "class_name": self.class_name,
        }


@dataclass
class RouteContract:
    """Flask/FastAPI HTTP route kontratı."""
    file_path: str
    endpoint: str                     # "/api/chat"
    methods: List[str]               # ["POST", "GET"]
    handler_name: str                # "api_chat_stream"
    request_params: List[str]        # Beklenen request parametreleri
    response_keys: List[str]         # JSON response'daki key'ler
    status_codes: List[int]          # Dönen HTTP status code'lar

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file": self.file_path,
            "endpoint": self.endpoint,
            "methods": self.methods,
            "handler": self.handler_name,
            "request_params": self.request_params,
            "response_keys": self.response_keys,
            "status_codes": self.status_codes,
        }


@dataclass
class FrontendContract:
    """Frontend element kontratı."""
    file_path: str
    element_ids: List[str]           # HTML id'leri
    css_classes: List[str]           # Kullanılan CSS class'ları
    fetch_endpoints: List[str]       # fetch() ile çağrılan endpoint'ler
    response_keys_read: List[str]    # data.response, data.reply gibi okunan key'ler
    event_listeners: List[str]       # Bağlanan event listener'lar

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file": self.file_path,
            "element_ids": self.element_ids,
            "css_classes": self.css_classes,
            "fetch_endpoints": self.fetch_endpoints,
            "response_keys_read": self.response_keys_read,
            "event_listeners": self.event_listeners,
        }


@dataclass
class FileContracts:
    """Bir dosyanın tüm kontratlarını taşır."""
    file_path: str
    language: str
    functions: List[FunctionContract] = field(default_factory=list)
    routes: List[RouteContract] = field(default_factory=list)
    frontend: Optional[FrontendContract] = None
    classes: List[str] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    exports: List[str] = field(default_factory=list)
    extracted_at: float = field(default_factory=time.time)
    content_hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "file": self.file_path,
            "language": self.language,
            "classes": self.classes,
            "imports": self.imports,
            "exports": self.exports,
            "extracted_at": self.extracted_at,
            "content_hash": self.content_hash,
            "functions": [f.to_dict() for f in self.functions],
            "routes": [r.to_dict() for r in self.routes],
        }
        if self.frontend:
            result["frontend"] = self.frontend.to_dict()
        return result

    def to_compact_prompt(self) -> str:
        """
        AI prompt'una eklenecek kompakt kontrat özeti.
        Token tasarrufu için minimum bilgi, maksimum netlik.
        """
        lines = [f"[CONTRACT: {self.file_path}]"]

        for cls in self.classes:
            lines.append(f"  class {cls}")

        for func in self.functions:
            params_str = ", ".join(
                f"{p['name']}: {p.get('type', 'Any')}" for p in func.params
            )
            prefix = f"  {func.class_name}." if func.class_name else "  "
            lines.append(f"{prefix}{func.name}({params_str}) -> {func.returns}")

        for route in self.routes:
            methods_str = ",".join(route.methods)
            resp_str = ", ".join(route.response_keys[:5])
            lines.append(f"  [{methods_str}] {route.endpoint} -> {{{resp_str}}}")

        if self.frontend:
            if self.frontend.fetch_endpoints:
                lines.append(f"  fetch: {', '.join(self.frontend.fetch_endpoints[:8])}")
            if self.frontend.response_keys_read:
                lines.append(f"  reads: {', '.join(self.frontend.response_keys_read[:8])}")
            if self.frontend.element_ids:
                lines.append(f"  ids: {', '.join(self.frontend.element_ids[:10])}")

        return "\n".join(lines)


# ── Kontrat Çıkarıcılar (Extractors) ─────────────────────────

class PythonContractExtractor:
    """Python kaynak kodundan fonksiyon/route kontratları çıkarır (AST kullanır)."""

    def extract(self, source_code: str, file_path: str) -> FileContracts:
        contracts = FileContracts(
            file_path=file_path,
            language="python",
            content_hash=hashlib.sha256(source_code.encode()).hexdigest()[:16],
        )

        try:
            tree = ast.parse(source_code, filename=file_path)
        except SyntaxError as e:
            logger.warning(f"[CONTRACT] Python parse hatası {file_path}: {e}")
            return contracts

        # Import'ları çıkar
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    contracts.imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    contracts.imports.append(f"{module}.{alias.name}")

        # Top-level fonksiyonlar ve sınıflar
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                contracts.classes.append(node.name)
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        func = self._extract_function(item, file_path, class_name=node.name)
                        contracts.functions.append(func)

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func = self._extract_function(node, file_path)
                contracts.functions.append(func)

        # Route kontratları
        contracts.routes = self._extract_routes(source_code, file_path)

        return contracts

    def _extract_function(
        self,
        node: ast.FunctionDef,
        file_path: str,
        class_name: str = "",
    ) -> FunctionContract:
        # Parametreler
        params = []
        for arg in node.args.args:
            if arg.arg == "self":
                continue
            param = {"name": arg.arg, "type": "Any"}
            if arg.annotation:
                param["type"] = ast.unparse(arg.annotation) if hasattr(ast, "unparse") else str(arg.annotation)
            params.append(param)

        # Dönüş tipi
        returns = "None"
        if node.returns:
            returns = ast.unparse(node.returns) if hasattr(ast, "unparse") else str(node.returns)

        # Dekoratörler
        decorators = []
        for dec in node.decorator_list:
            try:
                decorators.append(f"@{ast.unparse(dec)}" if hasattr(ast, "unparse") else f"@{dec}")
            except Exception:
                decorators.append("@unknown")

        # Docstring
        docstring = ""
        if (node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, (ast.Constant, ast.Str))):
            raw = node.body[0].value.value if isinstance(node.body[0].value, ast.Constant) else node.body[0].value.s
            docstring = str(raw).strip().split("\n")[0][:100]

        # Bağımlılıklar (fonksiyon içindeki çağrıları bul)
        deps = []
        for child in ast.walk(node):
            if isinstance(child, ast.Attribute):
                try:
                    obj = ast.unparse(child.value) if hasattr(ast, "unparse") else ""
                    if obj and child.attr:
                        dep = f"{obj}.{child.attr}"
                        if dep not in deps and len(deps) < 10:
                            deps.append(dep)
                except Exception:
                    pass

        return FunctionContract(
            file_path=file_path,
            name=node.name,
            params=params,
            returns=returns,
            decorators=decorators,
            docstring=docstring,
            dependencies=deps[:10],
            line_number=node.lineno,
            is_method=bool(class_name),
            class_name=class_name,
        )

    def _extract_routes(self, source_code: str, file_path: str) -> List[RouteContract]:
        """Flask/FastAPI route dekoratörlerinden kontrat çıkarır."""
        routes = []

        # Flask: @app.route("/path", methods=["POST"])
        flask_pattern = r'@app\.(?:route|get|post|put|delete|patch)\(\s*["\']([^"\']+)["\'](?:.*?methods\s*=\s*\[([^\]]*)\])?\s*\)\s*\n\s*def\s+(\w+)'
        for match in re.finditer(flask_pattern, source_code, re.DOTALL):
            endpoint = match.group(1)
            methods_raw = match.group(2)
            handler = match.group(3)

            if methods_raw:
                methods = [m.strip().strip("'\"").upper() for m in methods_raw.split(",")]
            else:
                # Dekoratöre göre method belirle
                line = source_code[max(0, match.start() - 5):match.start() + 50]
                if ".get(" in line:
                    methods = ["GET"]
                elif ".post(" in line:
                    methods = ["POST"]
                elif ".put(" in line:
                    methods = ["PUT"]
                elif ".delete(" in line:
                    methods = ["DELETE"]
                else:
                    methods = ["GET"]

            # Response key'lerini bul
            func_match = re.search(
                rf'def\s+{re.escape(handler)}\s*\([^)]*\)\s*:\s*(.*?)(?=\ndef\s|\Z)',
                source_code,
                re.DOTALL,
            )
            response_keys = []
            request_params = []
            status_codes = [200]

            if func_match:
                func_body = func_match.group(1)
                # jsonify({"key": ...}) veya {"key": ...}
                response_keys = list(set(re.findall(r'["\'](\w+)["\']\s*:', func_body)))[:10]
                # request.json.get("key") veya data.get("key")
                request_params = list(set(re.findall(r'\.get\(\s*["\'](\w+)["\']', func_body)))[:10]
                # HTTP status codes
                status_codes = list(set(
                    [200] + [int(m) for m in re.findall(r'\),\s*(\d{3})', func_body)]
                ))

            routes.append(RouteContract(
                file_path=file_path,
                endpoint=endpoint,
                methods=methods,
                handler_name=handler,
                request_params=request_params,
                response_keys=response_keys,
                status_codes=sorted(status_codes),
            ))

        return routes


class JavaScriptContractExtractor:
    """JavaScript kaynak kodundan kontrat çıkarır (regex tabanlı)."""

    def extract(self, source_code: str, file_path: str) -> FileContracts:
        contracts = FileContracts(
            file_path=file_path,
            language="javascript",
            content_hash=hashlib.sha256(source_code.encode()).hexdigest()[:16],
        )

        # Fonksiyonlar
        # function name(params) veya const name = (params) => veya async function
        func_patterns = [
            r'(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)',
            r'(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(?([^)]*?)\)?\s*=>',
        ]
        for pattern in func_patterns:
            for match in re.finditer(pattern, source_code):
                name = match.group(1)
                params_str = match.group(2)
                params = [
                    {"name": p.strip(), "type": "Any"}
                    for p in params_str.split(",")
                    if p.strip()
                ]
                contracts.functions.append(FunctionContract(
                    file_path=file_path,
                    name=name,
                    params=params,
                    returns="Any",
                    decorators=[],
                    docstring="",
                    dependencies=[],
                    line_number=source_code[:match.start()].count("\n") + 1,
                ))

        # Class tanımları
        for match in re.finditer(r'class\s+(\w+)', source_code):
            contracts.classes.append(match.group(1))

        # Frontend kontratı
        contracts.frontend = self._extract_frontend(source_code, file_path)

        return contracts

    def _extract_frontend(self, source_code: str, file_path: str) -> FrontendContract:
        # Element ID'leri
        ids = list(set(re.findall(r'getElementById\(\s*["\']([^"\']+)["\']', source_code)))
        ids.extend(re.findall(r'querySelector\(\s*["\']#([^"\']+)["\']', source_code))
        ids = list(set(ids))

        # CSS class'ları
        css_classes = list(set(re.findall(r'classList\.(?:add|toggle|remove)\(\s*["\']([^"\']+)["\']', source_code)))

        # fetch endpoint'leri
        fetch_endpoints = list(set(re.findall(r'fetch\(\s*["\']([^"\']+)["\']', source_code)))

        # Okunan response key'leri
        response_keys = list(set(re.findall(r'(?:data|result|response|json)\.(\w+)\b', source_code)))

        # Event listener'lar
        listeners = list(set(re.findall(r'addEventListener\(\s*["\'](\w+)["\']', source_code)))

        return FrontendContract(
            file_path=file_path,
            element_ids=ids,
            css_classes=css_classes,
            fetch_endpoints=fetch_endpoints,
            response_keys_read=response_keys,
            event_listeners=listeners,
        )


class HTMLContractExtractor:
    """HTML'den element ID/class haritası çıkarır."""

    def extract(self, source_code: str, file_path: str) -> FileContracts:
        contracts = FileContracts(
            file_path=file_path,
            language="html",
            content_hash=hashlib.sha256(source_code.encode()).hexdigest()[:16],
        )

        ids = list(set(re.findall(r'id\s*=\s*["\']([^"\']+)["\']', source_code)))
        classes = set()
        for value in re.findall(r'class\s*=\s*["\']([^"\']+)["\']', source_code):
            classes.update(value.split())

        contracts.frontend = FrontendContract(
            file_path=file_path,
            element_ids=ids,
            css_classes=list(classes),
            fetch_endpoints=[],
            response_keys_read=[],
            event_listeners=[],
        )

        return contracts


class CSSContractExtractor:
    """CSS'den tanımlı class/id listesi çıkarır."""

    def extract(self, source_code: str, file_path: str) -> FileContracts:
        contracts = FileContracts(
            file_path=file_path,
            language="css",
            content_hash=hashlib.sha256(source_code.encode()).hexdigest()[:16],
        )

        css_classes = list(set(re.findall(r'\.([a-zA-Z_][\w-]*)', source_code)))
        css_ids = list(set(re.findall(r'#([a-zA-Z_][\w-]*)', source_code)))

        contracts.exports = css_classes[:50]
        contracts.frontend = FrontendContract(
            file_path=file_path,
            element_ids=css_ids,
            css_classes=css_classes,
            fetch_endpoints=[],
            response_keys_read=[],
            event_listeners=[],
        )

        return contracts


# ── Ana ContractLayer Sınıfı ──────────────────────────────────

class ContractLayer:
    """
    Obsidian Vault üzerine kontrat katmanı.

    Bir ajan kod yazdığında otomatik olarak:
    1. Fonksiyon imzalarını AST ile çıkarır
    2. Route kontratlarını regex ile çıkarır
    3. Frontend element haritasını oluşturur
    4. Vault'a mikro-not olarak kaydeder
    5. Diğer ajanlar sadece bu kontratı okur

    Bu sayede backend ve frontend %100 uyumlu olur.
    """

    EXTRACTORS = {
        ".py": PythonContractExtractor,
        ".js": JavaScriptContractExtractor,
        ".html": HTMLContractExtractor,
        ".htm": HTMLContractExtractor,
        ".css": CSSContractExtractor,
    }

    def __init__(self, vault=None):
        self._vault = vault
        self._contracts: Dict[str, FileContracts] = {}
        self._extractors = {ext: cls() for ext, cls in self.EXTRACTORS.items()}

    def extract_contracts(self, source_code: str, file_path: str) -> FileContracts:
        """
        Kaynak koddan kontratları çıkarır.
        Dosya uzantısına göre uygun extractor seçilir.
        """
        ext = os.path.splitext(file_path)[1].lower()
        extractor = self._extractors.get(ext)

        if not extractor:
            return FileContracts(
                file_path=file_path,
                language=ext.lstrip(".") or "unknown",
                content_hash=hashlib.sha256(source_code.encode()).hexdigest()[:16],
            )

        contracts = extractor.extract(source_code, file_path)
        self._contracts[file_path] = contracts
        return contracts

    def save_to_vault(self, contracts: FileContracts) -> Optional[str]:
        """Kontratları Obsidian Vault'a mikro-not olarak kaydeder."""
        if not self._vault:
            return None

        note_content = json.dumps(contracts.to_dict(), ensure_ascii=False, indent=2)
        title = f"contract:{contracts.file_path}"

        try:
            note = self._vault.create_note(
                title=title,
                content=note_content,
                note_type="contract",
                tags=["contract", contracts.language, "auto-extracted"],
                metadata={
                    "source_file": contracts.file_path,
                    "language": contracts.language,
                    "function_count": len(contracts.functions),
                    "route_count": len(contracts.routes),
                    "content_hash": contracts.content_hash,
                },
            )
            return note.note_id
        except Exception as e:
            logger.error(f"[CONTRACT] Vault kayıt hatası: {e}")
            return None

    def get_contracts(self, file_path: str) -> Optional[FileContracts]:
        """Bellekteki kontratları döndürür."""
        return self._contracts.get(file_path)

    def get_all_contracts(self) -> Dict[str, FileContracts]:
        """Tüm kontratları döndürür."""
        return dict(self._contracts)

    def build_cross_file_prompt(self, target_file: str, all_files: List[str] = None) -> str:
        """
        Bir dosya yazılırken diğer dosyaların kontratlarını
        kompakt prompt olarak döndürür.

        Bu prompt AI'a verildiğinde, dosyalar arası
        uyuşmazlık oluşma ihtimali sıfırlanır.
        """
        if all_files is None:
            all_files = list(self._contracts.keys())

        lines = ["[CROSS-FILE CONTRACTS — Bu kontratlarla %100 uyumlu kod yaz]"]

        for file_path in all_files:
            if file_path == target_file:
                continue
            contract = self._contracts.get(file_path)
            if contract:
                lines.append(contract.to_compact_prompt())

        return "\n".join(lines) if len(lines) > 1 else ""

    def validate_consistency(self) -> List[Dict[str, Any]]:
        """
        Tüm kontratlar arasındaki tutarlılığı kontrol eder.
        Uyuşmazlıkları raporlar.
        """
        issues = []

        # Backend route'ları topla
        all_routes = {}
        for file_path, contract in self._contracts.items():
            for route in contract.routes:
                all_routes[route.endpoint] = route

        # Backend response key'lerini topla
        all_response_keys = {}
        for file_path, contract in self._contracts.items():
            for route in contract.routes:
                all_response_keys[route.endpoint] = set(route.response_keys)

        # Frontend kontratlarını kontrol et
        for file_path, contract in self._contracts.items():
            if not contract.frontend:
                continue

            # 1. fetch endpoint kontrolü
            for endpoint in contract.frontend.fetch_endpoints:
                if endpoint.startswith("/") and endpoint not in all_routes:
                    if not endpoint.startswith("/static"):
                        issues.append({
                            "type": "endpoint_mismatch",
                            "severity": "error",
                            "file": file_path,
                            "message": f"Frontend '{endpoint}' endpoint'ine istek atıyor ama backend route'u yok",
                            "expected": list(all_routes.keys()),
                        })

            # 2. Response key kontrolü
            for key in contract.frontend.response_keys_read:
                for endpoint, resp_keys in all_response_keys.items():
                    if endpoint in contract.frontend.fetch_endpoints and resp_keys:
                        if key not in resp_keys and key not in {"type", "content", "error"}:
                            issues.append({
                                "type": "response_key_mismatch",
                                "severity": "warning",
                                "file": file_path,
                                "message": f"Frontend 'data.{key}' okuyor ama {endpoint} route'u bu key'i döndürmüyor",
                                "available_keys": list(resp_keys),
                            })

        # 3. HTML ID / JS ID eşleşmesi
        html_ids = set()
        js_ids = set()
        for file_path, contract in self._contracts.items():
            if not contract.frontend:
                continue
            if contract.language == "html":
                html_ids.update(contract.frontend.element_ids)
            elif contract.language == "javascript":
                js_ids.update(contract.frontend.element_ids)

        for missing_id in js_ids - html_ids:
            issues.append({
                "type": "element_id_mismatch",
                "severity": "error",
                "message": f"JS '#{missing_id}' id'sini arıyor ama HTML'de tanımlı değil",
                "available_ids": list(html_ids)[:20],
            })

        return issues

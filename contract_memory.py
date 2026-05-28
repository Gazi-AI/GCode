"""
GCode Contract Memory — Fonksiyon Bazlı Kontrat Hafıza Yöneticisi

Devasa kod bloklarını her istekte tekrar gönderip token sınırını
bitirmemek için, bir ajan bir dosyada fonksiyon yazdığı an, GCode
o fonksiyonun kontratını kaydeder. Diğer ajanlar dosya yerine
sadece kontratı okur.

Bu modül ContractLayer'ı sarmalayarak:
    - Otomatik kontrat yakalama (dosya yazıldığında)
    - Kontrat arama ve sorgulama
    - Çapraz dosya tutarlılık kontrolü
    - AI prompt'una kontrat enjeksiyonu
    - Obsidian Vault entegrasyonu
sağlar.
"""

import os
import json
import time
import logging
from typing import Optional, Dict, List, Any

from obsidian_vault.contract_layer import (
    ContractLayer,
    FileContracts,
    FunctionContract,
    RouteContract,
    FrontendContract,
)

logger = logging.getLogger("GCode.ContractMemory")


class ContractMemory:
    """
    GCode'un kontrat hafıza yöneticisi.

    Kullanım:
        memory = ContractMemory(vault=obsidian_vault_instance)

        # Dosya yazıldığında kontratı otomatik kaydet
        memory.register_file("app.py", source_code)

        # Başka bir dosya yazılırken kontrat bağlamını al
        context = memory.get_context_for("static/js/app.js")

        # Tutarlılık kontrolü
        issues = memory.validate_all()
    """

    def __init__(self, vault=None):
        self._contract_layer = ContractLayer(vault=vault)
        self._vault = vault
        self._file_hashes: Dict[str, str] = {}  # file -> content_hash (gereksiz yeniden çıkarmayı önler)
        self._last_validation: Dict[str, Any] = {}

    # ── Kontrat Kayıt ─────────────────────────────────────────

    def register_file(self, file_path: str, source_code: str, save_to_vault: bool = True) -> FileContracts:
        """
        Bir dosyanın kontratını çıkarır ve kaydeder.
        Aynı dosya değişmediyse yeniden çıkarmaz.
        """
        import hashlib
        content_hash = hashlib.sha256(source_code.encode()).hexdigest()[:16]

        # Aynı hash ise skip (performans)
        if self._file_hashes.get(file_path) == content_hash:
            existing = self._contract_layer.get_contracts(file_path)
            if existing:
                return existing

        contracts = self._contract_layer.extract_contracts(source_code, file_path)
        self._file_hashes[file_path] = content_hash

        if save_to_vault and self._vault:
            self._contract_layer.save_to_vault(contracts)

        logger.info(
            f"[CONTRACT] {file_path}: "
            f"{len(contracts.functions)} fonksiyon, "
            f"{len(contracts.routes)} route, "
            f"{len(contracts.classes)} class"
        )

        return contracts

    def register_contract(
        self,
        file_path: str,
        func_name: str,
        metadata: Dict[str, Any],
    ) -> None:
        """
        Manuel kontrat kaydı. AI'ın ürettiği bilgiden kontrat oluşturur.
        """
        contracts = self._contract_layer.get_contracts(file_path)
        if not contracts:
            ext = os.path.splitext(file_path)[1].lower()
            contracts = FileContracts(
                file_path=file_path,
                language=ext.lstrip(".") or "python",
            )

        func = FunctionContract(
            file_path=file_path,
            name=func_name,
            params=[
                {"name": p, "type": "Any"}
                for p in (metadata.get("params") or [])
            ],
            returns=metadata.get("returns", "Any"),
            decorators=metadata.get("decorators", []),
            docstring=metadata.get("docstring", ""),
            dependencies=metadata.get("dependencies", []),
            line_number=metadata.get("line", 0),
            class_name=metadata.get("class_name", ""),
            is_method=bool(metadata.get("class_name")),
        )

        # Aynı isimde varsa güncelle
        contracts.functions = [
            f for f in contracts.functions
            if f.name != func_name or f.class_name != func.class_name
        ]
        contracts.functions.append(func)

        # Route bilgisi varsa ekle
        if metadata.get("route"):
            route = RouteContract(
                file_path=file_path,
                endpoint=metadata["route"],
                methods=metadata.get("methods", ["GET"]),
                handler_name=func_name,
                request_params=metadata.get("request_params", []),
                response_keys=metadata.get("response_keys", []),
                status_codes=metadata.get("status_codes", [200]),
            )
            contracts.routes = [
                r for r in contracts.routes if r.endpoint != route.endpoint
            ]
            contracts.routes.append(route)

        self._contract_layer._contracts[file_path] = contracts

        if self._vault:
            self._contract_layer.save_to_vault(contracts)

    # ── Kontrat Sorgulama ─────────────────────────────────────

    def get_contract(self, file_path: str, func_name: Optional[str] = None) -> Optional[Dict]:
        """Belirli dosya/fonksiyon kontratını döndürür."""
        contracts = self._contract_layer.get_contracts(file_path)
        if not contracts:
            return None

        if func_name:
            for func in contracts.functions:
                if func.name == func_name:
                    return func.to_dict()
            for route in contracts.routes:
                if route.handler_name == func_name:
                    return route.to_dict()
            return None

        return contracts.to_dict()

    def get_all_contracts(self) -> Dict[str, Dict]:
        """Tüm dosyaların kontratlarını döndürür."""
        return {
            path: contract.to_dict()
            for path, contract in self._contract_layer.get_all_contracts().items()
        }

    def get_routes(self) -> List[Dict]:
        """Tüm HTTP route kontratlarını döndürür."""
        routes = []
        for contract in self._contract_layer.get_all_contracts().values():
            for route in contract.routes:
                routes.append(route.to_dict())
        return routes

    def search_contracts(self, query: str) -> List[Dict]:
        """Kontratlar arasında arama yapar."""
        query_lower = query.lower()
        results = []

        for file_path, contract in self._contract_layer.get_all_contracts().items():
            score = 0

            if query_lower in file_path.lower():
                score += 10

            for func in contract.functions:
                if query_lower in func.name.lower():
                    score += 5
                    results.append({
                        "type": "function",
                        "score": score,
                        **func.to_dict(),
                    })

            for route in contract.routes:
                if query_lower in route.endpoint.lower():
                    score += 5
                    results.append({
                        "type": "route",
                        "score": score,
                        **route.to_dict(),
                    })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:20]

    # ── AI Prompt Entegrasyonu ────────────────────────────────

    def get_context_for(self, target_file: str, max_tokens: int = 800) -> str:
        """
        Bir dosya yazılırken diğer dosyaların kontrat bağlamını
        AI prompt'u olarak döndürür.

        Token tasarrufu: Koca dosyaları göndermek yerine
        sadece kontrat özetlerini gönderir (~90% token tasarrufu).
        """
        prompt = self._contract_layer.build_cross_file_prompt(target_file)

        # Token limitini aşmamak için kes
        if len(prompt) > max_tokens * 4:  # ~4 char/token
            prompt = prompt[:max_tokens * 4] + "\n... [kontrat devam ediyor]"

        return prompt

    def inject_contracts_into_prompt(
        self,
        base_prompt: str,
        target_file: str,
        max_contract_chars: int = 3000,
    ) -> str:
        """
        Mevcut prompt'a kontrat bağlamını enjekte eder.
        Worker prompt'larına otomatik eklenir.
        """
        contract_context = self.get_context_for(target_file, max_tokens=max_contract_chars // 4)

        if not contract_context:
            return base_prompt

        return (
            f"{base_prompt}\n\n"
            f"─── KONTRAT HAFIZA (Bu bilgiler doğrudan kullan, dosya okuma) ───\n"
            f"{contract_context}\n"
            f"─── KONTRAT SONU ───\n\n"
            f"KURAL: Yukarıdaki kontratlarla %100 uyumlu kod yaz. "
            f"Endpoint isimleri, response key'leri ve element ID'leri değiştirme."
        )

    # ── Tutarlılık Kontrolü ───────────────────────────────────

    def validate_all(self) -> List[Dict[str, Any]]:
        """
        Tüm kontratlar arasındaki tutarlılığı kontrol eder.
        Frontend-backend uyuşmazlıklarını tespit eder.
        """
        issues = self._contract_layer.validate_consistency()
        self._last_validation = {
            "timestamp": time.time(),
            "issue_count": len(issues),
            "issues": issues,
        }
        return issues

    def validate_file_pair(self, file_a: str, file_b: str) -> List[Dict[str, Any]]:
        """İki dosya arasındaki kontrat uyumunu kontrol eder."""
        contract_a = self._contract_layer.get_contracts(file_a)
        contract_b = self._contract_layer.get_contracts(file_b)

        if not contract_a or not contract_b:
            return []

        issues = []

        # Backend-Frontend route eşleşmesi
        if contract_a.routes and contract_b.frontend:
            route_endpoints = {r.endpoint for r in contract_a.routes}
            for fetch_url in contract_b.frontend.fetch_endpoints:
                if fetch_url.startswith("/") and fetch_url not in route_endpoints:
                    if not fetch_url.startswith("/static"):
                        issues.append({
                            "type": "route_mismatch",
                            "file_a": file_a,
                            "file_b": file_b,
                            "message": f"{file_b} fetch('{fetch_url}') çağırıyor ama {file_a}'da bu route yok",
                        })

        # Tersi de kontrol et
        if contract_b.routes and contract_a.frontend:
            route_endpoints = {r.endpoint for r in contract_b.routes}
            for fetch_url in contract_a.frontend.fetch_endpoints:
                if fetch_url.startswith("/") and fetch_url not in route_endpoints:
                    if not fetch_url.startswith("/static"):
                        issues.append({
                            "type": "route_mismatch",
                            "file_a": file_b,
                            "file_b": file_a,
                            "message": f"{file_a} fetch('{fetch_url}') çağırıyor ama {file_b}'da bu route yok",
                        })

        return issues

    # ── Proje Taraması ────────────────────────────────────────

    def scan_project(self, project_dir: str, extensions: tuple = (".py", ".js", ".html", ".css")) -> int:
        """
        Proje dizinindeki tüm dosyaları tarayıp kontratlarını çıkarır.
        İlk başlatmada veya tam senkronizasyon için kullanılır.
        """
        skip_dirs = {"__pycache__", ".git", "node_modules", ".venv", "venv", "vault_data"}
        scanned = 0

        for root, dirs, filenames in os.walk(project_dir):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for filename in filenames:
                ext = os.path.splitext(filename)[1].lower()
                if ext not in extensions:
                    continue

                file_path = os.path.join(root, filename)
                rel_path = os.path.relpath(file_path, project_dir).replace("\\", "/")

                try:
                    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                        source = f.read()
                    if len(source) > 500000:  # 500KB üzeri skip
                        continue
                    self.register_file(rel_path, source, save_to_vault=False)
                    scanned += 1
                except (IOError, OSError):
                    continue

        logger.info(f"[CONTRACT] Proje taraması tamamlandı: {scanned} dosya")
        return scanned

    # ── Durum ve İstatistikler ────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Kontrat hafıza istatistikleri."""
        all_contracts = self._contract_layer.get_all_contracts()
        total_functions = sum(len(c.functions) for c in all_contracts.values())
        total_routes = sum(len(c.routes) for c in all_contracts.values())
        total_classes = sum(len(c.classes) for c in all_contracts.values())

        return {
            "total_files": len(all_contracts),
            "total_functions": total_functions,
            "total_routes": total_routes,
            "total_classes": total_classes,
            "files": list(all_contracts.keys()),
            "last_validation": self._last_validation,
        }

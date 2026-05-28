"""
GCode Async Orchestrator — Asenkron Multi-Agent Dosya Üretim Motoru

Extended ve Hyper pipeline'larında dosyaları sıralı değil
paralel olarak üretir. Böylece 4+ dosyalı projelerde ~4x hızlanma sağlar.

Özellikler:
    - concurrent.futures ile paralel dosya üretimi
    - Proxy havuzundan her worker'a farklı IP dağıtımı
    - ContractMemory ile otomatik tutarlılık sağlama
    - İlerleme takibi ve SSE event yayınlama
    - Hata durumunda retry ve fallback
    - Bağımlılık sırasına göre sıralama (dependency-aware)
"""

import os
import time
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from typing import Optional, Dict, Any, List, Callable, Generator
from dataclasses import dataclass, field

from tier_router import TierRouter
from contract_memory import ContractMemory

logger = logging.getLogger("GCode.AsyncOrchestrator")


@dataclass
class WorkerTask:
    """Tek bir dosya üretim görevi."""
    file_path: str
    purpose: str
    prompt: str
    depends_on: List[str] = field(default_factory=list)
    status: str = "waiting"         # waiting, running, done, failed, retrying
    result: Optional[Dict] = None   # {"path": "...", "content": "..."}
    error: Optional[str] = None
    start_time: float = 0.0
    end_time: float = 0.0
    retries: int = 0

    @property
    def elapsed_ms(self) -> float:
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time) * 1000
        return 0.0


@dataclass
class OrchestratorSession:
    """Bir proje üretim oturumu."""
    session_id: str
    plan: Dict[str, Any]
    tasks: List[WorkerTask]
    tier_name: str
    system_prompt: str = ""
    user_question: str = ""
    status: str = "created"     # created, running, done, failed
    created_at: float = field(default_factory=time.time)
    events: List[Dict] = field(default_factory=list)

    def add_event(self, event_type: str, data: Any):
        self.events.append({
            "type": event_type,
            "data": data,
            "timestamp": time.time(),
        })


class AsyncOrchestrator:
    """
    Asenkron Multi-Agent Dosya Üretim Motoru.

    Kullanım:
        orchestrator = AsyncOrchestrator(tier_router, contract_memory)
        session = orchestrator.create_session(plan, "extended", system_prompt)
        results = orchestrator.execute(session)
    """

    MAX_WORKERS = 4              # Eşzamanlı dosya üretimi
    MAX_RETRIES = 2              # Dosya başına retry sayısı
    WORKER_TIMEOUT = 180         # Worker başına timeout (saniye)

    def __init__(
        self,
        tier_router: TierRouter,
        contract_memory: ContractMemory,
    ):
        self._router = tier_router
        self._contract_memory = contract_memory
        self._sessions: Dict[str, OrchestratorSession] = {}
        self._lock = threading.RLock()

    # ── Session Yönetimi ──────────────────────────────────────

    def create_session(
        self,
        plan: Dict[str, Any],
        tier_name: str,
        system_prompt: str = "",
        user_question: str = "",
    ) -> OrchestratorSession:
        """Yeni bir üretim oturumu oluşturur."""
        import hashlib

        session_id = hashlib.md5(
            f"{time.time()}:{user_question[:50]}".encode()
        ).hexdigest()[:12]

        tasks = []
        for file_info in plan.get("files", []):
            if not isinstance(file_info, dict) or not file_info.get("path"):
                continue
            task = WorkerTask(
                file_path=file_info["path"],
                purpose=file_info.get("purpose", ""),
                prompt="",  # execute sırasında doldurulacak
                depends_on=file_info.get("depends_on", []),
            )
            tasks.append(task)

        session = OrchestratorSession(
            session_id=session_id,
            plan=plan,
            tasks=tasks,
            tier_name=tier_name,
            system_prompt=system_prompt,
            user_question=user_question,
        )

        self._sessions[session_id] = session
        return session

    # ── Bağımlılık Sıralama ──────────────────────────────────

    def _topological_sort(self, tasks: List[WorkerTask]) -> List[List[WorkerTask]]:
        """
        Bağımlılıklara göre task'ları katmanlara ayırır.
        Her katman paralel çalışabilir, katmanlar arası sıralı.
        """
        path_to_task = {t.file_path: t for t in tasks}
        in_degree = {t.file_path: 0 for t in tasks}

        for task in tasks:
            for dep in task.depends_on:
                if dep in path_to_task:
                    in_degree[task.file_path] = in_degree.get(task.file_path, 0) + 1

        # Katmanları oluştur
        layers = []
        remaining = set(t.file_path for t in tasks)

        while remaining:
            # Bağımlılığı olmayan (veya bağımlılıkları çözümlenmiş) task'lar
            ready = {
                path for path in remaining
                if in_degree.get(path, 0) == 0
            }

            if not ready:
                # Döngüsel bağımlılık — kalanları hep birlikte çalıştır
                ready = remaining.copy()

            layer = [path_to_task[path] for path in ready if path in path_to_task]
            layers.append(layer)
            remaining -= ready

            # Çözümlenen bağımlılıkları güncelle
            for path in ready:
                for task in tasks:
                    if path in task.depends_on:
                        in_degree[task.file_path] = max(0, in_degree.get(task.file_path, 1) - 1)

        return layers

    # ── Ana Execution ─────────────────────────────────────────

    def execute(
        self,
        session: OrchestratorSession,
        prompt_builder: Callable = None,
        on_progress: Callable = None,
    ) -> List[Dict[str, Any]]:
        """
        Oturumdaki tüm dosyaları üretir.

        Args:
            session: Üretim oturumu
            prompt_builder: (file_info, plan, user_question, contract_context) -> str
            on_progress: (event_type, data) -> None

        Returns:
            Üretilen dosya listesi: [{"path": "...", "content": "..."}]
        """
        session.status = "running"
        results = []

        # Bağımlılık katmanlarına ayır
        layers = self._topological_sort(session.tasks)

        for layer_idx, layer in enumerate(layers):
            self._emit(session, on_progress, "layer_start", {
                "layer": layer_idx,
                "files": [t.file_path for t in layer],
                "message": f"Katman {layer_idx + 1}/{len(layers)}: {len(layer)} dosya paralel üretiliyor",
            })

            # Bu katmanı paralel çalıştır
            layer_results = self._execute_layer(
                session=session,
                tasks=layer,
                prompt_builder=prompt_builder,
                on_progress=on_progress,
            )

            # Sonuçları kontrat hafızasına kaydet
            for file_data in layer_results:
                if isinstance(file_data, dict) and file_data.get("content"):
                    self._contract_memory.register_file(
                        file_data["path"],
                        file_data["content"],
                        save_to_vault=True,
                    )

            results.extend(layer_results)

        session.status = "done"

        # Son tutarlılık kontrolü
        issues = self._contract_memory.validate_all()
        if issues:
            self._emit(session, on_progress, "consistency_issues", {
                "count": len(issues),
                "issues": [f"{i.get('message', '')}" for i in issues[:5]],
            })

        return results

    def _execute_layer(
        self,
        session: OrchestratorSession,
        tasks: List[WorkerTask],
        prompt_builder: Callable = None,
        on_progress: Callable = None,
    ) -> List[Dict[str, Any]]:
        """Bir katmandaki task'ları paralel olarak çalıştırır."""
        results = []
        max_workers = min(self.MAX_WORKERS, len(tasks))

        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="GCodeWorker") as executor:
            futures: Dict[Future, WorkerTask] = {}

            for task in tasks:
                # Prompt oluştur
                if prompt_builder:
                    contract_context = self._contract_memory.get_context_for(task.file_path)
                    file_info = next(
                        (f for f in session.plan.get("files", [])
                         if isinstance(f, dict) and f.get("path") == task.file_path),
                        {"path": task.file_path, "purpose": task.purpose},
                    )
                    task.prompt = prompt_builder(
                        file_info,
                        session.plan,
                        session.user_question,
                        contract_context,
                    )
                else:
                    task.prompt = self._default_prompt(task, session)

                future = executor.submit(
                    self._generate_single_file,
                    session,
                    task,
                    on_progress,
                )
                futures[future] = task

            for future in as_completed(futures, timeout=self.WORKER_TIMEOUT):
                task = futures[future]
                try:
                    result = future.result(timeout=self.WORKER_TIMEOUT)
                    if result:
                        results.append(result)
                except Exception as e:
                    task.status = "failed"
                    task.error = str(e)
                    logger.error(f"[ORCHESTRATOR] {task.file_path} worker exception: {e}")
                    self._emit(session, on_progress, "worker_error", {
                        "path": task.file_path,
                        "error": str(e),
                    })

        return results

    def _generate_single_file(
        self,
        session: OrchestratorSession,
        task: WorkerTask,
        on_progress: Callable = None,
    ) -> Optional[Dict[str, Any]]:
        """Tek bir dosyayı üretir (worker thread'de çalışır)."""
        task.status = "running"
        task.start_time = time.time()

        self._emit(session, on_progress, "worker_start", {
            "path": task.file_path,
            "purpose": task.purpose,
        })

        for retry in range(self.MAX_RETRIES + 1):
            if retry > 0:
                task.status = "retrying"
                task.retries = retry
                self._emit(session, on_progress, "worker_retry", {
                    "path": task.file_path,
                    "retry": retry,
                })

            try:
                # LLM'den streaming yanıt al
                response_text = ""
                messages = [{"role": "user", "content": task.prompt}]

                for chunk in self._router.stream(
                    tier_name=session.tier_name,
                    messages=messages,
                    system_prompt=session.system_prompt,
                    temperature=0.2,
                ):
                    response_text += chunk

                if not response_text.strip() or len(response_text.strip()) < 20:
                    continue  # Boş cevap — retry

                # JSON parse
                file_data = self._parse_worker_response(response_text, task.file_path)
                if file_data and file_data.get("content"):
                    task.status = "done"
                    task.result = file_data
                    task.end_time = time.time()

                    self._emit(session, on_progress, "worker_done", {
                        "path": task.file_path,
                        "chars": len(file_data["content"]),
                        "elapsed_ms": round(task.elapsed_ms),
                    })

                    return file_data

            except Exception as e:
                logger.error(f"[ORCHESTRATOR] {task.file_path} retry {retry}: {e}")
                time.sleep(2 * (retry + 1))

        task.status = "failed"
        task.end_time = time.time()
        task.error = "Tüm denemeler başarısız"
        return None

    def _parse_worker_response(self, response_text: str, expected_path: str) -> Optional[Dict]:
        """Worker yanıtından dosya verisini çıkarır."""
        import re

        # JSON formatı: {"path": "...", "content": "..."}
        # Markdown fence temizle
        cleaned = response_text.strip()
        cleaned = re.sub(r'^```(?:json)?\s*\n?', '', cleaned)
        cleaned = re.sub(r'\n?```\s*$', '', cleaned)

        try:
            data = json.loads(cleaned)
            if isinstance(data, dict):
                path = data.get("path", expected_path)
                content = data.get("content", "")
                if content:
                    return {"path": path, "content": content}
        except json.JSONDecodeError:
            pass

        # JSON parse başarısız — raw text olarak dön
        if len(response_text.strip()) > 50:
            return {
                "path": expected_path,
                "content": response_text.strip(),
            }

        return None

    def _default_prompt(self, task: WorkerTask, session: OrchestratorSession) -> str:
        """Varsayılan dosya üretim prompt'u."""
        contract_context = self._contract_memory.get_context_for(task.file_path)

        prompt = (
            f"Generate the file '{task.file_path}' for the project.\n"
            f"Purpose: {task.purpose}\n\n"
            f"User request: {session.user_question}\n\n"
            f"Project plan:\n{json.dumps(session.plan, ensure_ascii=False)[:4000]}\n\n"
        )

        if contract_context:
            prompt += (
                f"Cross-file contracts (MUST be consistent):\n{contract_context}\n\n"
            )

        prompt += (
            'Return JSON only: {"path": "relative/path", "content": "full file content"}\n'
            "The file must be complete, runnable, and consistent with all contracts."
        )

        return prompt

    def _emit(self, session, callback, event_type, data):
        """Event yayınlar."""
        session.add_event(event_type, data)
        if callback:
            try:
                callback(event_type, data)
            except Exception as e:
                logger.error(f"[ORCHESTRATOR] Event callback hatası: {e}")

    # ── Durum ve İstatistikler ────────────────────────────────

    def get_session_status(self, session_id: str) -> Optional[Dict]:
        session = self._sessions.get(session_id)
        if not session:
            return None

        return {
            "session_id": session.session_id,
            "status": session.status,
            "tier": session.tier_name,
            "total_tasks": len(session.tasks),
            "tasks": [
                {
                    "path": t.file_path,
                    "status": t.status,
                    "retries": t.retries,
                    "elapsed_ms": round(t.elapsed_ms),
                    "error": t.error,
                }
                for t in session.tasks
            ],
            "events": session.events[-20:],
        }

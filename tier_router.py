"""
GCode Tier Router — Katmanlı Model Yönlendirme Motoru

Gelen istekleri seçilen model katmanına (Core, Extended, Hyper)
göre doğru pipeline'a yönlendirir.

Katmanlar:
    Core     — Pollinations openai, hızlı hata düzeltme, thinking levels
    Extended — g4f Yqcloud, hızlı yanıt
    Hyper    — g4f Opera Aria (GPT-4/Gemini altyapılı), en güçlü katman
"""

import os
import time
import json
import logging
import threading
from typing import Optional, Dict, Any, Generator, List
from dataclasses import dataclass, field

import requests

# g4f entegrasyonu
try:
    import g4f
    from g4f.client import Client as G4FClient
    _HAS_G4F = True
except ImportError:
    _HAS_G4F = False

logger = logging.getLogger("GCode.TierRouter")


# ── Thinking Level Konfigürasyonu ─────────────────────────────

@dataclass
class ThinkingConfig:
    """Core tier thinking level ayarları."""
    level: int               # 1-5 arası
    temperature: float       # Model sıcaklığı
    max_tokens: int          # Maksimum token sayısı
    system_suffix: str       # Sistem promptuna eklenecek talimat

    @classmethod
    def from_level(cls, level: int) -> "ThinkingConfig":
        level = max(1, min(5, level))
        configs = {
            1: cls(1, 0.3, 2048, "Kisa ve net cevap ver. Analiz yapma."),
            2: cls(2, 0.5, 3072, "Ozet dusunme ile cevap ver."),
            3: cls(3, 0.7, 4096, "Dengeli analiz ve cevap ver."),
            4: cls(4, 0.8, 4096, "Derin analiz yap, alternatifler dusun."),
            5: cls(5, 0.9, 4096, "Cok katmanli analiz yap. Her aciyi degerlendir. En kapsamli cevabi ver."),
        }
        return configs[level]


# ── Tier Tanımları ────────────────────────────────────────────

class BaseTier:
    """Tüm tier'ların temel sınıfı."""

    TIER_NAME = "base"
    TIER_DISPLAY = "Base"
    COLOR_ACCENT = "#888888"
    COLOR_ACCENT2 = "#aaaaaa"

    def __init__(self, session: requests.Session = None):
        self._session = session or requests.Session()
        self._lock = threading.RLock()
        self._last_request_at = 0.0
        self._request_gap = 2  # saniye

    def _wait_for_slot(self):
        elapsed = time.time() - self._last_request_at
        wait = max(0, self._request_gap - elapsed)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.time()

    def stream(
        self,
        messages: List[Dict],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> Generator[str, None, None]:
        raise NotImplementedError

    def get_info(self) -> Dict[str, Any]:
        return {
            "tier": self.TIER_NAME,
            "display": self.TIER_DISPLAY,
            "accent": self.COLOR_ACCENT,
            "accent2": self.COLOR_ACCENT2,
        }


class CoreTier(BaseTier):
    """
    GaziGPT Core — Hızlı kodlama ve genel görevler.

    Pollinations openai modeli üzerinden çalışır.
    Thinking levels ile akıl yürütme derinliği ayarlanabilir.
    """

    TIER_NAME = "core"
    TIER_DISPLAY = "GaziGPT"
    COLOR_ACCENT = "#8a2be2"
    COLOR_ACCENT2 = "#20d6a5"

    POLLINATIONS_URL = "https://text.pollinations.ai/openai/chat/completions"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._request_gap = 2

    def stream(
        self,
        messages: List[Dict],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        thinking_level: int = 3,
    ) -> Generator[str, None, None]:
        thinking = ThinkingConfig.from_level(thinking_level)
        temperature = thinking.temperature
        max_tokens = thinking.max_tokens

        full_messages = [{"role": "system", "content": system_prompt}] if system_prompt else []
        if thinking.system_suffix:
            suffix_msg = f"\n\n[THINKING LEVEL {thinking.level}] {thinking.system_suffix}"
            if full_messages:
                full_messages[0]["content"] += suffix_msg
            else:
                full_messages.append({"role": "system", "content": suffix_msg})

        full_messages.extend(messages[-10:])

        with self._lock:
            self._wait_for_slot()

        try:
            resp = self._session.post(
                self.POLLINATIONS_URL,
                json={
                    "messages": full_messages,
                    "model": "openai",
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "stream": True,
                },
                headers={
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                },
                timeout=(10, 300),
                stream=True,
            )

            if resp.status_code != 200:
                yield f"Core API hatası (HTTP {resp.status_code})"
                return

            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
                except json.JSONDecodeError:
                    pass

        except Exception as e:
            yield f"Core bağlantı hatası: {e}"


class ExtendedTier(BaseTier):
    """
    GaziGPT Extended — g4f Yqcloud provider ile hızlı yanıt.
    """

    TIER_NAME = "extended"
    TIER_DISPLAY = "GaziGPT Extended"
    COLOR_ACCENT = "#0066ff"
    COLOR_ACCENT2 = "#00d4ff"
    G4F_PROVIDER_NAME = "Yqcloud"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._request_gap = 1
        self._g4f_client = G4FClient() if _HAS_G4F else None

    def stream(
        self,
        messages: List[Dict],
        system_prompt: str = "",
        temperature: float = 0.5,
        max_tokens: int = 8192,
    ) -> Generator[str, None, None]:
        if not _HAS_G4F or self._g4f_client is None:
            yield "g4f kütüphanesi yüklü değil. pip install g4f"
            return

        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages[-16:])

        text_received = False
        try:
            provider_class = getattr(g4f.Provider, self.G4F_PROVIDER_NAME, None)
            if provider_class is None:
                yield f"g4f provider '{self.G4F_PROVIDER_NAME}' bulunamadı."
                return

            response = self._g4f_client.chat.completions.create(
                model="",
                provider=provider_class,
                messages=full_messages,
                stream=True,
            )
            for chunk in response:
                content = chunk.choices[0].delta.content or ""
                if content:
                    text_received = True
                    yield content
        except Exception as e:
            logger.error(f"[EXTENDED] g4f {self.G4F_PROVIDER_NAME} hatası: {e}")
            if not text_received:
                yield f"Extended ({self.G4F_PROVIDER_NAME}) bağlantı hatası: {e}"


class HyperTier(BaseTier):
    """
    GaziGPT Hyper — g4f Opera Aria (GPT-4/Gemini altyapılı).
    En güçlü katman.
    """

    TIER_NAME = "hyper"
    TIER_DISPLAY = "GaziGPT Hyper"
    COLOR_ACCENT = "#bf00ff"
    COLOR_ACCENT2 = "#ffd700"
    G4F_PROVIDER_NAME = "OperaAria"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._request_gap = 1
        self._g4f_client = G4FClient() if _HAS_G4F else None

    def stream(
        self,
        messages: List[Dict],
        system_prompt: str = "",
        temperature: float = 0.6,
        max_tokens: int = 4096,
    ) -> Generator[str, None, None]:
        if not _HAS_G4F or self._g4f_client is None:
            yield "g4f kütüphanesi yüklü değil. pip install g4f"
            return

        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages[-10:])

        text_received = False
        try:
            provider_class = getattr(g4f.Provider, self.G4F_PROVIDER_NAME, None)
            if provider_class is None:
                yield f"g4f provider '{self.G4F_PROVIDER_NAME}' bulunamadı."
                return

            response = self._g4f_client.chat.completions.create(
                model="",
                provider=provider_class,
                messages=full_messages,
                stream=True,
            )
            for chunk in response:
                content = chunk.choices[0].delta.content or ""
                if content:
                    text_received = True
                    yield content
        except Exception as e:
            logger.error(f"[HYPER] g4f {self.G4F_PROVIDER_NAME} hatası: {e}")
            if not text_received:
                yield f"Hyper ({self.G4F_PROVIDER_NAME}) bağlantı hatası: {e}"

    def get_info(self) -> Dict[str, Any]:
        info = super().get_info()
        info["provider"] = self.G4F_PROVIDER_NAME
        return info


# ── Ana Router ────────────────────────────────────────────────

class TierRouter:
    """
    Katmanlı Model Yönlendirme Motoru.

    Gelen istekleri seçilen tier'a yönlendirir.
    Tier'lar arası otomatik fallback sağlar.

    Kullanım:
        router = TierRouter()
        for chunk in router.stream("core", messages, thinking_level=3):
            print(chunk, end="")
    """

    TIER_MAP = {
        "core": CoreTier,
        "extended": ExtendedTier,
        "hyper": HyperTier,
    }

    # Frontend model adı → tier adı eşleşmesi
    MODEL_TO_TIER = {
        "GaziGPT": "core",
        "GaziGPT Thinking": "core",  # Core + yüksek thinking level
        "GaziGPT Extended": "extended",
        "GaziGPT Hyper": "hyper",
    }

    # Tier → renk şeması
    TIER_COLORS = {
        "core": {"accent": "#8a2be2", "accent2": "#20d6a5", "bg": "#111318"},
        "extended": {"accent": "#0066ff", "accent2": "#00d4ff", "bg": "#0a1628"},
        "hyper": {"accent": "#bf00ff", "accent2": "#ffd700", "bg": "#0a0015"},
    }

    def __init__(self):
        self._session = requests.Session()
        self._tiers: Dict[str, BaseTier] = {}
        self._initialize_tiers()

    def _initialize_tiers(self):
        """Tüm tier'ları başlatır."""
        self._tiers["core"] = CoreTier(session=self._session)
        self._tiers["extended"] = ExtendedTier(session=self._session)
        self._tiers["hyper"] = HyperTier(session=self._session)
        logger.info("[TIER] Tüm katmanlar başlatıldı: Core (Pollinations), Extended (Yqcloud), Hyper (OperaAria)")

    def resolve_tier(self, model_name: str) -> str:
        """Frontend model adından tier adına çözümler."""
        return self.MODEL_TO_TIER.get(model_name, "core")

    def get_tier(self, tier_name: str) -> BaseTier:
        """Tier instance döndürür."""
        return self._tiers.get(tier_name, self._tiers["core"])

    def stream(
        self,
        tier_name: str,
        messages: List[Dict],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        thinking_level: int = 3,
    ) -> Generator[str, None, None]:
        """
        Belirtilen tier üzerinden streaming yanıt alır.
        Başarısız olursa fallback chain: hyper → extended → core
        """
        tier = self.get_tier(tier_name)

        kwargs = {
            "messages": messages,
            "system_prompt": system_prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        # Core tier'a thinking level ekle
        if tier_name == "core" and isinstance(tier, CoreTier):
            kwargs["thinking_level"] = thinking_level

        text_received = False
        try:
            for chunk in tier.stream(**kwargs):
                if chunk and chunk.strip():
                    text_received = True
                yield chunk
        except Exception as e:
            logger.error(f"[TIER] {tier_name} hatası: {e}")

        # Fallback: bir alt katmana düş
        if not text_received:
            fallback_chain = {"hyper": "extended", "extended": "core"}
            fallback_tier = fallback_chain.get(tier_name)
            if fallback_tier:
                logger.warning(f"[TIER] {tier_name} başarısız, {fallback_tier} fallback")
                yield f"\n[{tier_name} → {fallback_tier} fallback]\n"
                yield from self.stream(
                    fallback_tier,
                    messages,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

    def get_colors(self, tier_name: str) -> Dict[str, str]:
        """Tier'a ait renk şemasını döndürür."""
        return self.TIER_COLORS.get(tier_name, self.TIER_COLORS["core"])

    def get_status(self) -> Dict[str, Any]:
        """Tüm tier'ların durumunu döndürür."""
        return {
            "tiers": {
                name: tier.get_info()
                for name, tier in self._tiers.items()
            },
            "model_map": self.MODEL_TO_TIER,
            "colors": self.TIER_COLORS,
        }

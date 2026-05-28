"""
GaziGPT Obsidian Vault - Devasa Bilgi Yönetim Sistemi

Bu paket, AI'ın 4096 karakter sınırını aşmasını sağlayan
chunk-tabanlı kod üretme, bilgi depolama ve akıllı birleştirme
sistemidir.

Modüller:
    - vault_core: Ana vault motoru, not CRUD işlemleri
    - chunk_writer: Büyük kodları parçalara bölme ve yazma
    - assembler: Parçaları birleştirip tam dosya oluşturma
    - graph: Not bağlantı haritası (dependency graph)
    - indexer: Vault içerik indeksleme ve arama
    - templates: Kod şablonları ve iskelet yapılar
    - context_manager: AI bağlam yönetimi ve bellek optimizasyonu
    - quality_gate: Parça kalite kontrolü
"""

__version__ = "1.0.0"
__author__ = "GaziGPT"

from obsidian_vault.vault_core import ObsidianVault
from obsidian_vault.chunk_writer import ChunkWriter
from obsidian_vault.assembler import CodeAssembler
from obsidian_vault.graph import DependencyGraph
from obsidian_vault.indexer import VaultIndexer
from obsidian_vault.context_manager import ContextManager
from obsidian_vault.quality_gate import QualityGate

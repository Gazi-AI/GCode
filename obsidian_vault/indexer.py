"""
VaultIndexer - Vault İçerik İndeksleme ve Tam Metin Arama

Tüm notları indeksler, n-gram tabanlı arama yapar,
benzerlik hesaplar ve semantik ilişkiler kurar.
"""

import re
import os
import json
import math
import hashlib
from typing import Optional, Dict, List, Set, Any, Tuple
from collections import Counter, defaultdict

from obsidian_vault.vault_core import ObsidianVault, VaultNote


class VaultIndexer:
    """
    Vault için tam metin arama motoru.
    
    Özellikler:
        - TF-IDF tabanlı arama
        - N-gram indeksleme
        - Fuzzy matching
        - Tag ve tip bazlı filtreleme
        - İçerik benzerlik analizi
    """

    # Türkçe stop words
    STOP_WORDS = frozenset({
        "bir", "ve", "de", "da", "bu", "su", "ile", "icin", "için", "ama",
        "veya", "gibi", "daha", "en", "her", "olan", "olan", "olarak",
        "var", "yok", "ben", "sen", "biz", "siz", "onlar", "kadar",
        "ise", "ki", "ne", "nasil", "nasıl", "neden", "hangi",
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "must", "shall",
        "can", "need", "dare", "ought", "used", "to", "of", "in",
        "for", "on", "with", "at", "by", "from", "as", "into",
        "through", "during", "before", "after", "above", "below",
        "between", "under", "that", "this", "these", "those",
        "not", "no", "nor", "if", "or", "and", "but", "so",
        "self", "cls", "def", "class", "return", "import", "from",
        "true", "false", "none", "pass",
    })

    def __init__(self, vault: ObsidianVault):
        self.vault = vault
        self.tf_idf_index: Dict[str, Dict[str, float]] = {}  # {token: {note_id: tfidf_score}}
        self.doc_freq: Dict[str, int] = {}  # {token: doc_count}
        self.doc_lengths: Dict[str, int] = {}  # {note_id: total_tokens}
        self.ngram_index: Dict[str, Set[str]] = {}  # {ngram: {note_id, ...}}
        self._build_index()

    def _tokenize(self, text: str) -> List[str]:
        """Metni token'lara ayırır."""
        text = text.lower()
        text = re.sub(r"[^a-zA-Z0-9çğıöşüâîûÇĞİÖŞÜ_]", " ", text)
        tokens = [t for t in text.split() if len(t) > 1 and t not in self.STOP_WORDS]
        return tokens

    def _ngrams(self, text: str, n: int = 3) -> List[str]:
        """N-gram'lar oluşturur."""
        text = text.lower()
        return [text[i:i + n] for i in range(len(text) - n + 1)]

    def _build_index(self) -> None:
        """Tüm vault'u indeksler."""
        self.tf_idf_index.clear()
        self.doc_freq.clear()
        self.doc_lengths.clear()
        self.ngram_index.clear()

        # Doküman frekanslarını hesapla
        doc_tokens: Dict[str, List[str]] = {}
        for note_id, note in self.vault.notes.items():
            full_text = f"{note.title} {' '.join(note.tags)} {note.content}"
            tokens = self._tokenize(full_text)
            doc_tokens[note_id] = tokens
            self.doc_lengths[note_id] = len(tokens)

            unique_tokens = set(tokens)
            for token in unique_tokens:
                self.doc_freq[token] = self.doc_freq.get(token, 0) + 1

        total_docs = max(len(self.vault.notes), 1)

        # TF-IDF hesapla
        for note_id, tokens in doc_tokens.items():
            if not tokens:
                continue
            token_counts = Counter(tokens)
            max_freq = max(token_counts.values())

            for token, count in token_counts.items():
                tf = 0.5 + 0.5 * (count / max_freq)  # Normalized TF
                idf = math.log(total_docs / (1 + self.doc_freq.get(token, 0)))
                score = tf * idf

                self.tf_idf_index.setdefault(token, {})[note_id] = score

        # N-gram indeksi oluştur
        for note_id, note in self.vault.notes.items():
            full_text = f"{note.title} {note.content}"
            for ngram in self._ngrams(full_text, 3):
                self.ngram_index.setdefault(ngram, set()).add(note_id)

    def rebuild(self) -> None:
        """İndeksi yeniden oluşturur."""
        self._build_index()

    # ── Arama ─────────────────────────────────────────────────

    def search(
        self,
        query: str,
        note_type: Optional[str] = None,
        tags: Optional[List[str]] = None,
        limit: int = 20,
    ) -> List[Tuple[float, VaultNote]]:
        """
        TF-IDF tabanlı arama yapar.
        
        Returns:
            (skor, not) çiftlerinin listesi
        """
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        scores: Dict[str, float] = defaultdict(float)

        for token in query_tokens:
            if token in self.tf_idf_index:
                for note_id, score in self.tf_idf_index[token].items():
                    scores[note_id] += score

        # Normalize
        for note_id in scores:
            doc_len = max(self.doc_lengths.get(note_id, 1), 1)
            scores[note_id] /= math.sqrt(doc_len)

        # Filtreleme
        results: List[Tuple[float, VaultNote]] = []
        for note_id, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
            note = self.vault.get_note(note_id)
            if note is None:
                continue
            if note_type and note.note_type != note_type:
                continue
            if tags and not any(t in note.tags for t in tags):
                continue
            results.append((score, note))
            if len(results) >= limit:
                break

        return results

    def fuzzy_search(self, query: str, limit: int = 10) -> List[Tuple[float, VaultNote]]:
        """N-gram tabanlı fuzzy arama yapar."""
        query_ngrams = set(self._ngrams(query.lower(), 3))
        if not query_ngrams:
            return []

        scores: Dict[str, float] = defaultdict(float)
        for ngram in query_ngrams:
            for note_id in self.ngram_index.get(ngram, set()):
                scores[note_id] += 1.0

        # Jaccard benzerliği ile normalize et
        for note_id in scores:
            note = self.vault.get_note(note_id)
            if note:
                note_ngrams = set(self._ngrams(f"{note.title} {note.content}".lower(), 3))
                intersection = len(query_ngrams & note_ngrams)
                union = len(query_ngrams | note_ngrams)
                scores[note_id] = intersection / max(union, 1)

        results: List[Tuple[float, VaultNote]] = []
        for note_id, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
            note = self.vault.get_note(note_id)
            if note and score > 0.01:
                results.append((score, note))
            if len(results) >= limit:
                break

        return results

    # ── Benzerlik Analizi ─────────────────────────────────────

    def find_similar(self, note_id: str, limit: int = 5) -> List[Tuple[float, VaultNote]]:
        """Bir nota en benzer notları bulur (cosine similarity)."""
        target = self.vault.get_note(note_id)
        if not target:
            return []

        target_tokens = set(self._tokenize(f"{target.title} {target.content}"))
        if not target_tokens:
            return []

        # Target'ın TF-IDF vektörünü oluştur
        target_vector: Dict[str, float] = {}
        for token in target_tokens:
            if token in self.tf_idf_index and note_id in self.tf_idf_index[token]:
                target_vector[token] = self.tf_idf_index[token][note_id]

        if not target_vector:
            return []

        # Diğer notlarla cosine similarity hesapla
        similarities: List[Tuple[float, str]] = []
        target_mag = math.sqrt(sum(v ** 2 for v in target_vector.values()))

        for other_id in self.vault.notes:
            if other_id == note_id:
                continue

            dot_product = 0.0
            other_mag_sq = 0.0

            for token, target_score in target_vector.items():
                other_score = self.tf_idf_index.get(token, {}).get(other_id, 0.0)
                dot_product += target_score * other_score
                other_mag_sq += other_score ** 2

            if dot_product > 0:
                other_mag = math.sqrt(other_mag_sq)
                similarity = dot_product / (target_mag * max(other_mag, 1e-10))
                similarities.append((similarity, other_id))

        similarities.sort(reverse=True)

        results: List[Tuple[float, VaultNote]] = []
        for score, nid in similarities[:limit]:
            note = self.vault.get_note(nid)
            if note:
                results.append((score, note))

        return results

    # ── İstatistikler ─────────────────────────────────────────

    def get_top_terms(self, limit: int = 30) -> List[Tuple[str, int]]:
        """En sık kullanılan terimleri döndürür."""
        return sorted(self.doc_freq.items(), key=lambda x: x[1], reverse=True)[:limit]

    def get_index_stats(self) -> Dict[str, Any]:
        """İndeks istatistiklerini döndürür."""
        return {
            "total_documents": len(self.doc_lengths),
            "unique_tokens": len(self.doc_freq),
            "total_tokens": sum(self.doc_lengths.values()),
            "avg_doc_length": sum(self.doc_lengths.values()) / max(len(self.doc_lengths), 1),
            "ngram_entries": len(self.ngram_index),
        }

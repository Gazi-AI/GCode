"""
DependencyGraph - Not Bağlantı Haritası

Obsidian benzeri çift yönlü bağlantı (wiki-link) sistemi.
Notlar arası bağımlılıkları izler ve topological sort yapar.
"""

import os
import re
import json
from typing import Optional, Dict, List, Set, Any, Tuple
from collections import defaultdict, deque

from obsidian_vault.vault_core import ObsidianVault, VaultNote


class GraphNode:
    """Graf düğümü — bir notu temsil eder."""

    def __init__(self, note_id: str, title: str, note_type: str = "code"):
        self.note_id = note_id
        self.title = title
        self.note_type = note_type
        self.outgoing: Set[str] = set()  # Bu notun bağlı olduğu notlar
        self.incoming: Set[str] = set()  # Bu nota bağlı olan notlar

    def to_dict(self) -> Dict[str, Any]:
        return {
            "note_id": self.note_id,
            "title": self.title,
            "type": self.note_type,
            "outgoing": sorted(self.outgoing),
            "incoming": sorted(self.incoming),
            "out_degree": len(self.outgoing),
            "in_degree": len(self.incoming),
        }


class DependencyGraph:
    """
    Obsidian benzeri bağlantı grafı.
    
    Özellikler:
        - Çift yönlü bağlantı (backlink) takibi
        - Topological sort (bağımlılık sıralaması)
        - Döngü tespiti (circular dependency)
        - Orphan not tespiti
        - Cluster analizi (bağlı bileşenler)
        - Mermaid diyagram export
    """

    def __init__(self, vault: ObsidianVault):
        self.vault = vault
        self.nodes: Dict[str, GraphNode] = {}
        self.rebuild()

    def rebuild(self) -> None:
        """Vault'tan grafı yeniden oluşturur."""
        self.nodes.clear()

        for note_id, note in self.vault.notes.items():
            node = GraphNode(note_id, note.title, note.note_type)
            self.nodes[note_id] = node

        for note_id, note in self.vault.notes.items():
            node = self.nodes[note_id]
            for link in note.links:
                if link in self.nodes:
                    node.outgoing.add(link)
                    self.nodes[link].incoming.add(note_id)

            # İçerikteki [[wiki-link]] formatını da tara
            wiki_links = re.findall(r"\[\[([^\]]+)\]\]", note.content)
            for link in wiki_links:
                link = link.strip()
                if link in self.nodes and link != note_id:
                    node.outgoing.add(link)
                    self.nodes[link].incoming.add(note_id)

    # ── Sorgulama ─────────────────────────────────────────────

    def get_backlinks(self, note_id: str) -> List[str]:
        """Bir nota hangi notlardan bağlantı var."""
        node = self.nodes.get(note_id)
        return sorted(node.incoming) if node else []

    def get_outlinks(self, note_id: str) -> List[str]:
        """Bir not hangi notlara bağlı."""
        node = self.nodes.get(note_id)
        return sorted(node.outgoing) if node else []

    def get_neighbors(self, note_id: str) -> Set[str]:
        """Bir notun tüm komşuları (hem gelen hem giden)."""
        node = self.nodes.get(note_id)
        if not node:
            return set()
        return node.outgoing | node.incoming

    # ── Topological Sort ──────────────────────────────────────

    def topological_sort(self) -> Tuple[List[str], bool]:
        """
        Notları bağımlılık sırasına göre sıralar (Kahn's algorithm).
        
        Returns:
            (sıralı_liste, döngü_var_mı)
        """
        in_degree: Dict[str, int] = {nid: len(node.incoming) for nid, node in self.nodes.items()}
        queue = deque([nid for nid, deg in in_degree.items() if deg == 0])
        result: List[str] = []

        while queue:
            current = queue.popleft()
            result.append(current)
            for neighbor in self.nodes[current].outgoing:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        has_cycle = len(result) < len(self.nodes)
        return result, has_cycle

    def detect_cycles(self) -> List[List[str]]:
        """Döngüleri tespit eder ve döndürür."""
        cycles: List[List[str]] = []
        visited: Set[str] = set()
        rec_stack: Set[str] = set()

        def _dfs(node_id: str, path: List[str]) -> None:
            visited.add(node_id)
            rec_stack.add(node_id)
            path.append(node_id)

            for neighbor in self.nodes.get(node_id, GraphNode("", "")).outgoing:
                if neighbor not in visited:
                    _dfs(neighbor, path[:])
                elif neighbor in rec_stack:
                    cycle_start = path.index(neighbor)
                    cycle = path[cycle_start:] + [neighbor]
                    cycles.append(cycle)

            rec_stack.discard(node_id)

        for node_id in self.nodes:
            if node_id not in visited:
                _dfs(node_id, [])

        return cycles

    # ── Analiz ────────────────────────────────────────────────

    def find_orphans(self) -> List[str]:
        """Hiçbir bağlantısı olmayan notları bulur."""
        orphans = []
        for node_id, node in self.nodes.items():
            if not node.outgoing and not node.incoming:
                orphans.append(node_id)
        return orphans

    def find_hubs(self, min_connections: int = 3) -> List[Tuple[str, int]]:
        """En çok bağlantıya sahip hub notları bulur."""
        hubs = []
        for node_id, node in self.nodes.items():
            total = len(node.outgoing) + len(node.incoming)
            if total >= min_connections:
                hubs.append((node_id, total))
        hubs.sort(key=lambda x: x[1], reverse=True)
        return hubs

    def find_clusters(self) -> List[Set[str]]:
        """Bağlı bileşenleri (cluster) bulur."""
        visited: Set[str] = set()
        clusters: List[Set[str]] = []

        def _bfs(start: str) -> Set[str]:
            cluster: Set[str] = set()
            queue = deque([start])
            while queue:
                current = queue.popleft()
                if current in cluster:
                    continue
                cluster.add(current)
                for neighbor in self.get_neighbors(current):
                    if neighbor not in cluster:
                        queue.append(neighbor)
            return cluster

        for node_id in self.nodes:
            if node_id not in visited:
                cluster = _bfs(node_id)
                visited.update(cluster)
                clusters.append(cluster)

        clusters.sort(key=len, reverse=True)
        return clusters

    def get_dependency_chain(self, note_id: str) -> List[str]:
        """Bir notun tüm bağımlılık zincirini bulur (derinlik-önce)."""
        chain: List[str] = []
        visited: Set[str] = set()

        def _dfs(nid: str) -> None:
            if nid in visited:
                return
            visited.add(nid)
            node = self.nodes.get(nid)
            if node:
                for dep in sorted(node.outgoing):
                    _dfs(dep)
            chain.append(nid)

        _dfs(note_id)
        return chain

    # ── Export ────────────────────────────────────────────────

    def to_mermaid(self, max_nodes: int = 50) -> str:
        """Grafı Mermaid diyagramı olarak export eder."""
        lines = ["graph TD"]
        nodes_shown = 0

        for node_id, node in list(self.nodes.items())[:max_nodes]:
            safe_id = re.sub(r"[^a-zA-Z0-9_]", "_", node_id)
            short_title = node.title[:30]
            icon = {"code": "[FILE]", "chunk": "[CHUNK]", "doc": "[DOC]", "template": "[MANIFEST]"}.get(node.note_type, "[NOTE]")
            lines.append(f'    {safe_id}["{icon} {short_title}"]')
            nodes_shown += 1

        for node_id, node in list(self.nodes.items())[:max_nodes]:
            safe_src = re.sub(r"[^a-zA-Z0-9_]", "_", node_id)
            for target in node.outgoing:
                if target in dict(list(self.nodes.items())[:max_nodes]):
                    safe_tgt = re.sub(r"[^a-zA-Z0-9_]", "_", target)
                    lines.append(f"    {safe_src} --> {safe_tgt}")

        return "\n".join(lines)

    def to_adjacency_list(self) -> Dict[str, List[str]]:
        """Grafı adjacency list olarak döndürür."""
        return {nid: sorted(node.outgoing) for nid, node in self.nodes.items()}

    def get_stats(self) -> Dict[str, Any]:
        """Graf istatistiklerini döndürür."""
        total_edges = sum(len(n.outgoing) for n in self.nodes.values())
        orphans = self.find_orphans()
        cycles = self.detect_cycles()
        clusters = self.find_clusters()
        hubs = self.find_hubs()

        return {
            "total_nodes": len(self.nodes),
            "total_edges": total_edges,
            "orphan_count": len(orphans),
            "cycle_count": len(cycles),
            "cluster_count": len(clusters),
            "hub_count": len(hubs),
            "largest_cluster": len(clusters[0]) if clusters else 0,
            "avg_connections": total_edges / max(len(self.nodes), 1),
            "density": total_edges / max(len(self.nodes) * (len(self.nodes) - 1), 1),
        }

    def get_full_report(self) -> str:
        """Tam graf raporu oluşturur."""
        stats = self.get_stats()
        lines = [
            "# [GRAPH] Dependency Graph Report",
            "",
            f"**Düğüm:** {stats['total_nodes']} | **Kenar:** {stats['total_edges']} | "
            f"**Yoğunluk:** {stats['density']:.3f}",
            "",
        ]

        # Orphan'lar
        orphans = self.find_orphans()
        if orphans:
            lines.append(f"## [!] Orphan Notlar ({len(orphans)})")
            for oid in orphans[:20]:
                node = self.nodes[oid]
                lines.append(f"- {node.title} ({node.note_type})")
            lines.append("")

        # Hub'lar
        hubs = self.find_hubs()
        if hubs:
            lines.append(f"## [HUB] Hub Notlar ({len(hubs)})")
            for hid, count in hubs[:10]:
                node = self.nodes[hid]
                lines.append(f"- **{node.title}** — {count} bağlantı")
            lines.append("")

        # Döngüler
        cycles = self.detect_cycles()
        if cycles:
            lines.append(f"## [CYCLE] Döngüler ({len(cycles)})")
            for cycle in cycles[:5]:
                lines.append(f"- {' -> '.join(cycle)}")
            lines.append("")

        # Cluster'lar
        clusters = self.find_clusters()
        if clusters:
            lines.append(f"## [CLUSTER] Cluster'lar ({len(clusters)})")
            for i, cluster in enumerate(clusters[:10]):
                lines.append(f"- Cluster {i + 1}: {len(cluster)} not")
            lines.append("")

        # Mermaid diyagram
        lines.append("## [MAP] Graf Diyagramı")
        lines.append("```mermaid")
        lines.append(self.to_mermaid())
        lines.append("```")

        return "\n".join(lines)

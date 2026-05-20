"""
kcore.py — K-core decomposition del knowledge graph di Piombo Engine.

Responsabilità:
- Costruisce un grafo NetworkX speculare al Neo4j
- Calcola il k-core reale sulla topologia del grafo
- Aggiorna i valori su Neo4j via lore_graph.update_kcore_values()
- Espone get_kcore_map() per uso runtime (pagerank.py, hybrid.py)

Terminologia:
  k-core di un nodo = il massimo k tale per cui il nodo appartiene
  a un sotto-grafo in cui ogni nodo ha almeno k vicini.

  Nel contesto narrativo:
    k=1 → entità periferiche (personaggi minori, luoghi isolati)
    k=2 → entità secondarie (fazioni minori, oggetti chiave)
    k=3 → hub narrativi (personaggi centrali, zone nevralgiche)
"""

import logging
from typing import Any

import networkx as nx

from app.ai.graph.lore_graph import LoreGraph

logger = logging.getLogger(__name__)


class KCoreAnalyzer:
    """
    Calcola e mantiene la k-core decomposition del knowledge graph.

    Uso tipico (post-ingest, una tantum):
        analyzer = KCoreAnalyzer(lore_graph)
        kcore_map = analyzer.compute_and_persist()

    Uso runtime (retrieval):
        k = analyzer.get_kcore(node_id)
        top_nodes = analyzer.get_nodes_by_min_kcore(min_k=2)
    """

    def __init__(self, lore_graph: LoreGraph) -> None:
        self._graph = lore_graph
        self._nx_graph: nx.Graph | None = None
        self._kcore_map: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build_nx_graph(self) -> nx.Graph:
        """
        Scarica nodi e relazioni da Neo4j e costruisce un grafo NetworkX.

        Usiamo un grafo NON diretto (nx.Graph) per il k-core:
        la decomposition k-core è definita su grafi non diretti.
        Le relazioni direzionate di Neo4j diventano edges bidirezionali.
        """
        data = self._graph.get_all_nodes_and_edges()

        G = nx.Graph()

        for node in data["nodes"]:
            node_id = node["id"]
            if node_id is None:
                continue
            G.add_node(
                node_id,
                labels=node.get("labels", []),
                k_core_manual=node.get("k_core"),  # valore manuale dal JSON
            )

        for edge in data["edges"]:
            from_id = edge.get("from")
            to_id = edge.get("to")
            if from_id and to_id and G.has_node(from_id) and G.has_node(to_id):
                # Se esiste già l'edge (relazione inversa), non sovrascrivere
                if not G.has_edge(from_id, to_id):
                    G.add_edge(from_id, to_id, rel_type=edge.get("type", ""))

        self._nx_graph = G
        logger.info(
            "NetworkX graph built: %d nodes, %d edges",
            G.number_of_nodes(),
            G.number_of_edges(),
        )
        return G

    # ------------------------------------------------------------------
    # Compute
    # ------------------------------------------------------------------

    def compute_kcore(self) -> dict[str, int]:
        """
        Esegue la k-core decomposition e ritorna { node_id: k_value }.
        Richiede che build_nx_graph() sia già stato chiamato.
        """
        if self._nx_graph is None:
            raise RuntimeError("Call build_nx_graph() before compute_kcore()")

        # core_number() ritorna { node: k } per tutti i nodi
        kcore_map: dict[str, int] = nx.core_number(self._nx_graph)
        self._kcore_map = kcore_map

        # Log distribuzione per debug / ADR benchmarks
        distribution: dict[int, int] = {}
        for k in kcore_map.values():
            distribution[k] = distribution.get(k, 0) + 1
        logger.info("K-core distribution: %s", dict(sorted(distribution.items())))

        return kcore_map

    def compute_and_persist(self) -> dict[str, int]:
        """
        Pipeline completa: build → compute → update Neo4j.
        Chiamata una volta sola dopo l'ingest dei seed lore.
        Ritorna la kcore_map per uso immediato.
        """
        self.build_nx_graph()
        kcore_map = self.compute_kcore()
        updated = self._graph.update_kcore_values(kcore_map)
        logger.info("K-core persisted on %d nodes", updated)
        return kcore_map

    # ------------------------------------------------------------------
    # Runtime helpers — usati da pagerank.py e hybrid.py
    # ------------------------------------------------------------------

    def get_kcore(self, node_id: str) -> int:
        """
        Ritorna il k-core di un nodo specifico.
        Se la mappa non è in memoria, la ricostruisce da Neo4j.
        """
        if not self._kcore_map:
            self._load_kcore_from_neo4j()
        return self._kcore_map.get(node_id, 0)

    def get_nodes_by_min_kcore(self, min_k: int) -> list[str]:
        """
        Ritorna tutti i node_id con k_core >= min_k.
        Usato per filtrare il contesto durante il retrieval
        (non caricare entità periferiche se il contesto è ricco).
        """
        if not self._kcore_map:
            self._load_kcore_from_neo4j()
        return [nid for nid, k in self._kcore_map.items() if k >= min_k]

    def get_kcore_map(self) -> dict[str, int]:
        """Ritorna l'intera mappa { node_id: k_value }."""
        if not self._kcore_map:
            self._load_kcore_from_neo4j()
        return self._kcore_map

    def get_subgraph_stats(self, min_k: int) -> dict[str, Any]:
        """
        Stats del sotto-grafo formato dai nodi con k >= min_k.
        Utile per benchmarks.md: quanti nodi/edges sopravvivono al filtro?
        """
        if self._nx_graph is None:
            return {}

        nodes_in_shell = [n for n, k in self._kcore_map.items() if k >= min_k]
        subgraph = self._nx_graph.subgraph(nodes_in_shell)

        return {
            "min_k": min_k,
            "nodes": subgraph.number_of_nodes(),
            "edges": subgraph.number_of_edges(),
            "total_nodes": self._nx_graph.number_of_nodes(),
            "coverage_pct": round(
                subgraph.number_of_nodes() / max(self._nx_graph.number_of_nodes(), 1) * 100, 1
            ),
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_kcore_from_neo4j(self) -> None:
        """
        Carica i valori k_core già persistiti su Neo4j in memoria.
        Fallback quando KCoreAnalyzer viene istanziato a runtime
        senza rieseguire compute_and_persist().
        """
        data = self._graph.get_all_nodes_and_edges()
        self._kcore_map = {
            node["id"]: node.get("k_core", 0) or 0
            for node in data["nodes"]
            if node.get("id") is not None
        }
        logger.info("K-core map loaded from Neo4j: %d entries", len(self._kcore_map))
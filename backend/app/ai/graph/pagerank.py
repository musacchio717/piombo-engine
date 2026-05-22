"""
pagerank.py — Personalized PageRank per il pattern TeaRAG di Piombo Engine.

Responsabilità:
- Entity linking: mappa il testo libero del player sui nodi del grafo
- Calcola Personalized PageRank (PPR) con seed = entità menzionate + location corrente
- Ritorna nodi ranked per rilevanza contestuale, pesati anche dal k-core
- Output usato da hybrid.py per selezionare il contesto grafo da passare all'LLM

Riferimento teorico:
  TeaRAG usa PPR per propagare rilevanza dai nodi seed attraverso le relazioni,
  privilegiando nodi strutturalmente vicini ai seed E narrativamente importanti
  (k-core alto). Questo riduce i token necessari vs un retrieval flat.
"""

import logging
import re
from dataclasses import dataclass, field

import networkx as nx

from app.ai.graph.lore_graph import LoreGraph
from app.ai.graph.kcore import KCoreAnalyzer

logger = logging.getLogger(__name__)

# Peso relativo del k-core nel re-ranking finale.
# Score finale = ppr_score * (1 + KCORE_BOOST * k_value)
# Con KCORE_BOOST=0.3: un nodo k=3 vale 1.9x un nodo k=0 a parità di PPR.
KCORE_BOOST = 0.3

# Damping factor standard PageRank (probabilità di seguire un edge vs teletrasporto)
DAMPING = 0.85

# Numero massimo di nodi restituiti dal PPR prima del filtro k-core
PPR_TOP_K = 20


@dataclass
class RankedNode:
    """Nodo risultante dal PPR con score composito."""
    node_id: str
    name: str
    labels: list[str]
    ppr_score: float
    k_core: int
    final_score: float
    properties: dict = field(default_factory=dict)


class PersonalizedPageRank:
    """
    Calcola PPR sul grafo NetworkX per identificare i nodi più rilevanti
    dato un input testuale del player e la location corrente della sessione.

    Uso tipico (a ogni azione del player):
        ppr = PersonalizedPageRank(lore_graph, kcore_analyzer)
        ranked = ppr.rank(
            player_input="voglio parlare con il medico al checkpoint",
            current_location_id="loc_checkpoint_a1"
        )
        top_nodes = ranked[:5]  # passa a hybrid.py
    """

    def __init__(self, lore_graph: LoreGraph, kcore_analyzer: KCoreAnalyzer) -> None:
        self._graph = lore_graph
        self._kcore = kcore_analyzer
        self._nx_graph: nx.DiGraph | None = None
        self._name_to_id_cache: dict[str, str] | None = None

    # ------------------------------------------------------------------
    # Grafo diretto per PPR
    # ------------------------------------------------------------------

    def build_nx_digraph(self) -> nx.DiGraph:
        """
        Costruisce un DiGraph per il PageRank (diretto, a differenza di
        kcore.py che usa un grafo non diretto).
        Il DiGraph riflette le relazioni semantiche direzionate del lore
        (es. MEMBER_OF, CONTROLLED_BY) che hanno significato diverso
        in avanti e all'indietro.
        """
        data = self._graph.get_all_nodes_and_edges()
        kcore_map = self._kcore.get_kcore_map()

        G = nx.DiGraph()

        for node in data["nodes"]:
            nid = node.get("id")
            if nid is None:
                continue
            G.add_node(
                nid,
                k_core=kcore_map.get(nid, 0),
                labels=node.get("labels", []),
            )

        for edge in data["edges"]:
            from_id = edge.get("from")
            to_id = edge.get("to")
            if from_id and to_id and G.has_node(from_id) and G.has_node(to_id):
                G.add_edge(from_id, to_id, rel_type=edge.get("type", ""))
                # Aggiungi edge inverso con peso ridotto per permettere
                # propagazione bidirezionale nel PPR (es. location → character)
                if not G.has_edge(to_id, from_id):
                    G.add_edge(to_id, from_id, rel_type=f"INV_{edge.get('type', '')}")

        self._nx_graph = G
        logger.debug(
            "DiGraph built: %d nodes, %d edges",
            G.number_of_nodes(),
            G.number_of_edges(),
        )
        return G

    def _ensure_graph(self) -> nx.DiGraph:
        if self._nx_graph is None:
            self.build_nx_digraph()
        return self._nx_graph  # type: ignore

    # ------------------------------------------------------------------
    # Entity linking — mappa testo libero → nodi grafo
    # ------------------------------------------------------------------

    def entity_link(self, text: str) -> list[str]:
        """
        Trova i nodi del grafo menzionati (anche parzialmente) nel testo.
        Strategia semplice ma efficace per 30 entità:
          1. Tokenizza il testo in n-gram (1-3 parole)
          2. Match case-insensitive contro i nomi dei nodi Neo4j
          3. Ritorna i node_id dei match, ordinati per k-core desc

        Per Settimana 3 (LLM online) questo può essere potenziato con
        NER vera, ma per ora è sufficiente per il retrieval.
        """
        G = self._ensure_graph()

        # Costruisce lookup name → node_id dal grafo in memoria
        # (evita una query Neo4j a ogni azione)
        if self._name_to_id_cache is None:
            self._name_to_id_cache = {}
            for node_id in G.nodes():
                node_data = self._graph.get_node(node_id)
                if node_data and node_data.get("name"):
                    self._name_to_id_cache[node_data["name"].lower()] = node_id
            logger.debug("entity_link cache built: %d entries", len(self._name_to_id_cache))
        name_to_id = self._name_to_id_cache

        # Genera n-gram dal testo (1, 2, 3 token)
        tokens = re.findall(r"[\w']+", text.lower())
        candidates: set[str] = set()

        for n in range(1, 4):
            for i in range(len(tokens) - n + 1):
                ngram = " ".join(tokens[i : i + n])
                if ngram in name_to_id:
                    candidates.add(name_to_id[ngram])

        # Fallback: match parziale su Neo4j se nessun match esatto
        if not candidates:
            words = [t for t in tokens if len(t) > 3]  # ignora parole corte
            for word in words[:3]:  # max 3 query
                results = self._graph.search_nodes_by_name(word, limit=2)
                for r in results:
                    if r.get("id"):
                        candidates.add(r["id"])

        kcore_map = self._kcore.get_kcore_map()
        linked = sorted(candidates, key=lambda nid: kcore_map.get(nid, 0), reverse=True)
        logger.debug("Entity linking '%s' → %s", text[:50], linked)
        return linked

    # ------------------------------------------------------------------
    # Personalized PageRank
    # ------------------------------------------------------------------

    def compute_ppr(
        self,
        seed_node_ids: list[str],
        alpha: float = DAMPING,
    ) -> dict[str, float]:
        """
        Calcola Personalized PageRank con distribuzione iniziale
        uniforme sui seed_node_ids.

        alpha = damping factor (probabilità di seguire un edge).
        1-alpha = probabilità di teletrasportarsi a un seed node.

        Ritorna { node_id: ppr_score } per tutti i nodi del grafo.
        """
        G = self._ensure_graph()

        if not seed_node_ids:
            logger.warning("No seed nodes for PPR — returning uniform PageRank")
            return nx.pagerank(G, alpha=alpha)

        # Filtra seed che esistono nel grafo
        valid_seeds = [nid for nid in seed_node_ids if G.has_node(nid)]
        if not valid_seeds:
            logger.warning("Seed nodes not in graph: %s", seed_node_ids)
            return nx.pagerank(G, alpha=alpha)

        # Distribuzione personalizzata: uniforme sui seed
        personalization = {nid: 0.0 for nid in G.nodes()}
        weight_per_seed = 1.0 / len(valid_seeds)
        for nid in valid_seeds:
            personalization[nid] = weight_per_seed

        ppr_scores: dict[str, float] = nx.pagerank(
            G,
            alpha=alpha,
            personalization=personalization,
            max_iter=100,
            tol=1e-6,
        )
        return ppr_scores

    # ------------------------------------------------------------------
    # Pipeline principale
    # ------------------------------------------------------------------

    def rank(
        self,
        player_input: str,
        current_location_id: str | None = None,
        top_k: int = PPR_TOP_K,
        min_kcore: int = 0,
    ) -> list[RankedNode]:
        """
        Pipeline completa:
          1. Entity linking sul testo del player
          2. Aggiunge location corrente come seed permanente
          3. Calcola PPR
          4. Re-ranking con boost k-core (TeaRAG pattern)
          5. Filtra per min_kcore e ritorna top_k nodi

        Args:
            player_input:        Testo libero dell'azione del player
            current_location_id: ID nodo Neo4j della location corrente (seed fisso)
            top_k:               Numero massimo di nodi da ritornare
            min_kcore:           Filtra nodi con k_core < min_kcore

        Returns:
            Lista di RankedNode ordinata per final_score desc
        """
        # 1. Seed = entity linking + location corrente
        seed_ids = self.entity_link(player_input)
        if current_location_id:
            if current_location_id not in seed_ids:
                seed_ids.append(current_location_id)

        # Seed expansion: se entity linking non trova personaggi,
        # aggiungi i nodi connessi alla location corrente come seed aggiuntivi
        entity_seeds = [s for s in seed_ids if s != current_location_id]
        if current_location_id and not entity_seeds:
            neighborhood = self._graph.get_neighborhood(
                current_location_id, depth=1, min_kcore=0
            )
            for n in neighborhood.get("nodes", [])[:8]:
                nid = n.get("id")
                if nid and nid != current_location_id and nid not in seed_ids:
                    seed_ids.append(nid)
            logger.debug("Seed expansion: %d nodi aggiunti da %s", len(seed_ids) - 1, current_location_id)

        logger.debug("PPR seeds: %s", seed_ids)

        # 2. PPR
        ppr_scores = self.compute_ppr(seed_ids)

        # 3. Fetch proprietà nodi per i top candidati (pre-filtro)
        kcore_map = self._kcore.get_kcore_map()

        # Pre-filtra per k_core prima di fare query Neo4j costose
        candidates = [
            (nid, score)
            for nid, score in ppr_scores.items()
            if kcore_map.get(nid, 0) >= min_kcore
        ]
        # Ordina per PPR score e prendi top_k * 2 (margine per re-ranking)
        candidates.sort(key=lambda x: x[1], reverse=True)
        candidates = candidates[: top_k * 2]

        # 4. Fetch dati nodi + re-ranking con k-core boost
        node_ids = [nid for nid, _ in candidates]
        node_data_list = self._graph.get_nodes_by_ids(node_ids)
        node_data_map = {n["id"]: n for n in node_data_list if n.get("id")}

        ranked: list[RankedNode] = []
        for nid, ppr_score in candidates:
            node_data = node_data_map.get(nid, {})
            k = kcore_map.get(nid, 0)

            # Score finale: PPR * (1 + boost * k_core)
            final_score = ppr_score * (1 + KCORE_BOOST * k)

            ranked.append(
                RankedNode(
                    node_id=nid,
                    name=node_data.get("name", nid),
                    labels=node_data.get("labels", []),
                    ppr_score=ppr_score,
                    k_core=k,
                    final_score=final_score,
                    properties={
                        k: v
                        for k, v in node_data.items()
                        if k not in ("id", "name", "labels", "k_core")
                    },
                )
            )

        # 5. Sort finale e top_k
        ranked.sort(key=lambda n: n.final_score, reverse=True)
        result = ranked[:top_k]

        logger.debug(
            "PPR ranked top-%d: %s",
            len(result),
            [(n.name, round(n.final_score, 4)) for n in result],
        )
        return result
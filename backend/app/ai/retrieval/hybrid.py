"""
hybrid.py — Retrieval ibrido per Piombo Engine (TeaRAG pattern).

Responsabilità:
- Combina risultati vector search (Qdrant) e graph search (PPR)
- Fonde e de-duplica i risultati con uno score composito
- Restituisce il contesto finale da passare all'LLM narratore

Flusso:
    player_input + current_location
        ↓
    [Vector search]          [Graph PPR]
    Qdrant descriptions  +   PersonalizedPageRank
    Qdrant triplets          ↓ nodi ranked
        ↓                    neighborhood Neo4j
    scored chunks            scored nodes
        ↓                         ↓
              [Fusion + dedup]
                    ↓
            HybridContext
            (testo pronto per l'LLM)
"""

import logging
from dataclasses import dataclass, field

from app.ai.retrieval.qdrant_ingestor import QdrantIngestor
from app.ai.graph.pagerank import PersonalizedPageRank, RankedNode
from app.ai.graph.lore_graph import LoreGraph

logger = logging.getLogger(__name__)

# Pesi relativi delle tre sorgenti nel fusion score
# Somma non deve fare 1.0 — sono moltiplicatori indipendenti
WEIGHT_DESCRIPTION = 1.0   # chunk descrittivi Qdrant
WEIGHT_TRIPLET     = 1.2   # triplets Qdrant (più densi, premiati)
WEIGHT_GRAPH       = 1.5   # nodi PPR (contesto strutturale, premiato di più)

# Numero risultati da richiedere a ogni sorgente prima della fusione
VECTOR_TOP_K = 8
GRAPH_TOP_K  = 10

# Soglia k-core minima per il retrieval (0 = nessun filtro)
# In produzione alzare a 1 per escludere entità completamente isolate
MIN_KCORE = 0

# Budget per sorgente nel contesto finale.
# Evita che i description chunks (score Qdrant ~0.8) soffochino
# i graph chunks (score PPR ~0.04) nel sort globale.
BUDGET_DESCRIPTION = 4
BUDGET_TRIPLET     = 3
BUDGET_GRAPH       = 4


@dataclass
class ContextChunk:
    """
    Unità atomica di contesto da passare all'LLM.
    Può provenire da Qdrant (description/triplet) o dal grafo (PPR node).
    """
    text: str
    score: float
    source: str          # "description" | "triplet" | "graph"
    node_id: str | None = None
    node_name: str | None = None
    k_core: int = 0
    metadata: dict = field(default_factory=dict)


@dataclass
class HybridContext:
    """
    Output finale del retrieval ibrido.
    Contiene i chunk pronti per essere inseriti nel prompt dell'LLM.
    """
    chunks: list[ContextChunk]
    seed_nodes: list[str]          # nodi usati come seed PPR
    total_tokens_estimate: int     # stima token (len chars / 4)

    def to_prompt_text(self, max_chunks: int = 10) -> str:
        """
        Serializza i top chunk in testo strutturato per il prompt.
        Formato leggibile dall'LLM con sezioni separate per tipo.
        """
        top = sorted(self.chunks, key=lambda c: c.score, reverse=True)[:max_chunks]

        descriptions = [c for c in top if c.source == "description"]
        triplets     = [c for c in top if c.source == "triplet"]
        graph_nodes  = [c for c in top if c.source == "graph"]

        sections: list[str] = []

        if descriptions:
            section = "## Contesto narrativo\n"
            section += "\n".join(f"- {c.text}" for c in descriptions)
            sections.append(section)

        if triplets:
            section = "## Relazioni note\n"
            section += "\n".join(f"- {c.text}" for c in triplets)
            sections.append(section)

        if graph_nodes:
            section = "## Entità rilevanti\n"
            section += "\n".join(
                f"- {c.node_name} (k={c.k_core}): {c.text}" for c in graph_nodes
            )
            sections.append(section)

        return "\n\n".join(sections)


class HybridRetriever:
    """
    Retrieval ibrido: vector search + graph PPR.

    Uso tipico (a ogni azione del player, dentro il Narrator Agent):
        retriever = HybridRetriever(qdrant_ingestor, ppr, lore_graph)
        context = retriever.retrieve(
            player_input="voglio attraversare il checkpoint",
            current_location_id="loc_checkpoint_a1"
        )
        prompt_text = context.to_prompt_text(max_chunks=8)
    """

    def __init__(
        self,
        qdrant: QdrantIngestor,
        ppr: PersonalizedPageRank,
        lore_graph: LoreGraph,
    ) -> None:
        self._qdrant    = qdrant
        self._ppr       = ppr
        self._lore_graph = lore_graph

    # ------------------------------------------------------------------
    # Pipeline principale
    # ------------------------------------------------------------------

    def retrieve(
        self,
        player_input: str,
        current_location_id: str | None = None,
        max_chunks: int = 10,
        min_kcore: int = MIN_KCORE,
    ) -> HybridContext:
        """
        Esegue il retrieval ibrido e restituisce HybridContext.

        Args:
            player_input:        Testo libero dell'azione del player
            current_location_id: Location corrente della sessione (seed PPR fisso)
            max_chunks:          Numero massimo di chunk nel contesto finale
            min_kcore:           Filtra entità con k_core < min_kcore
        """
        # 1. Vector search su Qdrant
        desc_results    = self._search_descriptions(player_input, min_kcore)
        triplet_results = self._search_triplets(player_input, min_kcore)

        # 2. Graph search via PPR
        ranked_nodes = self._ppr.rank(
            player_input=player_input,
            current_location_id=current_location_id,
            top_k=GRAPH_TOP_K,
            min_kcore=min_kcore,
        )

        # 3. Converti in ContextChunk con score pesato e applica budget per sorgente.
        # Budget separato perché gli score PPR (~0.04) sono incomparabili
        # con gli score Qdrant (~0.85) — un sort globale soffoca sempre il grafo.
        desc_chunks = sorted(
            self._desc_to_chunks(desc_results),
            key=lambda c: c.score, reverse=True
        )[:BUDGET_DESCRIPTION]

        triplet_chunks = sorted(
            self._triplet_to_chunks(triplet_results),
            key=lambda c: c.score, reverse=True
        )[:BUDGET_TRIPLET]

        graph_chunks = sorted(
            self._graph_to_chunks(ranked_nodes),
            key=lambda c: c.score, reverse=True
        )[:BUDGET_GRAPH]

        # 4. Dedup solo per stessa sorgente (description vs graph sono informazioni diverse)
        chunks = self._dedup(desc_chunks + triplet_chunks + graph_chunks)

        # Seed nodes usati (per logging / Langfuse)
        seed_nodes = self._ppr.entity_link(player_input)
        if current_location_id:
            seed_nodes = list(set(seed_nodes + [current_location_id]))

        total_chars = sum(len(c.text) for c in chunks)
        token_estimate = total_chars // 4

        logger.info(
            "HybridRetriever: %d chunks, ~%d tokens (desc=%d, triplets=%d, graph=%d)",
            len(chunks),
            token_estimate,
            sum(1 for c in chunks if c.source == "description"),
            sum(1 for c in chunks if c.source == "triplet"),
            sum(1 for c in chunks if c.source == "graph"),
        )

        return HybridContext(
            chunks=chunks,
            seed_nodes=seed_nodes,
            total_tokens_estimate=token_estimate,
        )

    # ------------------------------------------------------------------
    # Sorgenti individuali
    # ------------------------------------------------------------------

    def _search_descriptions(
        self, query: str, min_kcore: int
    ) -> list[dict]:
        return self._qdrant.search_descriptions(
            query=query,
            top_k=VECTOR_TOP_K,
            min_kcore=min_kcore,
        )

    def _search_triplets(
        self, query: str, min_kcore: int
    ) -> list[dict]:
        return self._qdrant.search_triplets(
            query=query,
            top_k=VECTOR_TOP_K,
            min_kcore=min_kcore,
        )

    # ------------------------------------------------------------------
    # Conversione in ContextChunk
    # ------------------------------------------------------------------

    def _desc_to_chunks(self, results: list[dict]) -> list[ContextChunk]:
        chunks = []
        for r in results:
            meta = r.get("metadata", {})
            node_id = meta.get("node_id")
            # Recupera description completa da Neo4j (Qdrant salva solo metadata)
            description = ""
            if node_id:
                node_data = self._lore_graph.get_node(node_id)
                if node_data:
                    description = node_data.get("description", "")
            text = description or self._rebuild_desc_text(meta)
            chunks.append(ContextChunk(
                text=text,
                score=r["score"] * WEIGHT_DESCRIPTION,
                source="description",
                node_id=node_id,
                node_name=meta.get("node_name"),
                k_core=meta.get("k_core", 0),
                metadata=meta,
            ))
        return chunks

    def _triplet_to_chunks(self, results: list[dict]) -> list[ContextChunk]:
        chunks = []
        for r in results:
            meta = r.get("metadata", {})
            text = (
                f"{meta.get('subject_name', '?')} "
                f"{meta.get('relation_type', '').lower().replace('_', ' ')} "
                f"{meta.get('object_name', '?')}"
            )
            chunks.append(ContextChunk(
                text=text,
                score=r["score"] * WEIGHT_TRIPLET,
                source="triplet",
                node_id=meta.get("subject_id"),
                node_name=meta.get("subject_name"),
                k_core=meta.get("max_kcore", 0),
                metadata=meta,
            ))
        return chunks

    def _graph_to_chunks(self, ranked_nodes: list[RankedNode]) -> list[ContextChunk]:
        chunks = []
        for node in ranked_nodes:
            # Fetch description dal grafo per i top nodi PPR
            node_data = self._lore_graph.get_node(node.node_id)
            description = ""
            if node_data:
                description = node_data.get("description", "")

            chunks.append(ContextChunk(
                text=description or node.name,
                score=node.final_score * WEIGHT_GRAPH,
                source="graph",
                node_id=node.node_id,
                node_name=node.name,
                k_core=node.k_core,
                metadata=node.properties,
            ))
        return chunks

    # ------------------------------------------------------------------
    # De-duplicazione
    # ------------------------------------------------------------------

    def _dedup(self, chunks: list[ContextChunk]) -> list[ContextChunk]:
        """
        Se lo stesso node_id compare in più sorgenti,
        tieni solo il chunk con score più alto.
        Chunk senza node_id (es. triplets cross-node) vengono mantenuti tutti.
        """
        seen: dict[str, ContextChunk] = {}
        no_id: list[ContextChunk] = []

        for chunk in chunks:
            if chunk.node_id is None:
                no_id.append(chunk)
                continue
            existing = seen.get(chunk.node_id)
            if existing is None or chunk.score > existing.score:
                seen[chunk.node_id] = chunk

        return list(seen.values()) + no_id

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _rebuild_desc_text(self, meta: dict) -> str:
        """
        Ricostruisce il testo leggibile dal payload Qdrant.
        Qdrant salva il metadata ma non il testo originale —
        lo ricaviamo dai campi del payload.
        """
        label = meta.get("node_label", "Entity")
        name  = meta.get("node_name", "")
        # La description completa non è nel payload per risparmiare spazio.
        # La recuperiamo da Neo4j solo se necessario (graph_to_chunks lo fa già).
        # Qui restituiamo un testo sintetico dal payload.
        return f"[{label}] {name}"
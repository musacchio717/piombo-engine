"""
triplet.py — Estrazione triplets dal knowledge graph di Piombo Engine.

Responsabilità:
- Estrae triplets testuali deterministici dalle relazioni Neo4j
- Formatta i triplets per l'ingest in Qdrant (insieme ai chunk descrittivi)
- I triplets sono la rappresentazione testuale delle relazioni del grafo,
  usati dal retrieval ibrido per aumentare la precisione semantica

Pattern TeaRAG:
  Ogni relazione Neo4j diventa un triplet testuale:
    (soggetto, predicato, oggetto)
  Esempio:
    ("Marco Ferretti", "fa parte di", "Colonna dei Sopravvissuti Nord")

  I triplets sono più densi informativamente dei chunk descrittivi:
  comprimono una relazione in ~10 token invece di 50-100.
  Questo è il motivo del ~60% di risparmio token citato nell'architettura.
"""

import logging
from dataclasses import dataclass

from app.ai.graph.lore_graph import LoreGraph

logger = logging.getLogger(__name__)

# Mappa tipo relazione Neo4j → predicato in linguaggio naturale (italiano)
RELATION_TO_PREDICATE: dict[str, str] = {
    "MEMBER_OF":     "fa parte di",
    "LOCATED_IN":    "si trova a",
    "KNOWS":         "conosce",
    "POSSESSES":     "possiede",
    "INVOLVED_IN":   "è coinvolto in",
    "CONTROLLED_BY": "è controllato da",
    "CONNECTED_TO":  "è collegato a",
    "HOSTILE_TO":    "è ostile a",
    "ALLIED_WITH":   "è alleato con",
    "OCCURRED_IN":   "è avvenuto a",
    "CAUSED_BY":     "è stato causato da",
}


@dataclass
class Triplet:
    """
    Triplet testuale estratto da una relazione Neo4j.

    subject_id / object_id: node_id Neo4j (per tracciabilità)
    text: stringa leggibile usata per l'embedding
    metadata: dizionario passato a Qdrant come payload per il filtering
    """
    subject_id: str
    subject_name: str
    predicate: str
    object_id: str
    object_name: str
    relation_type: str          # tipo originale Neo4j (es. "MEMBER_OF")
    relation_properties: dict
    subject_kcore: int
    object_kcore: int

    @property
    def text(self) -> str:
        """Rappresentazione testuale per embedding."""
        return f"{self.subject_name} {self.predicate} {self.object_name}"

    @property
    def metadata(self) -> dict:
        """Payload Qdrant per filtering e tracciabilità."""
        return {
            "type":              "triplet",
            "subject_id":        self.subject_id,
            "subject_name":      self.subject_name,
            "object_id":         self.object_id,
            "object_name":       self.object_name,
            "relation_type":     self.relation_type,
            "subject_kcore":     self.subject_kcore,
            "object_kcore":      self.object_kcore,
            "max_kcore":         max(self.subject_kcore, self.object_kcore),
            **{f"rel_{k}": v for k, v in self.relation_properties.items()},
        }


class TripletExtractor:
    """
    Estrae triplets deterministici dalle relazioni Neo4j.

    Uso tipico (post-ingest, una tantum):
        extractor = TripletExtractor(lore_graph)
        triplets = extractor.extract_all()
        # → passa a QdrantIngestor per l'embedding

    I triplets vengono rigenerati ogni volta che cambia il grafo
    (es. dopo un nuovo ingest di lore). Non hanno stato persistito
    separatamente — sono derivati deterministicamente dal grafo.
    """

    def __init__(self, lore_graph: LoreGraph) -> None:
        self._graph = lore_graph

    def extract_all(self) -> list[Triplet]:
        """
        Estrae tutti i triplets dal grafo Neo4j.
        Ritorna lista di Triplet ordinata per max_kcore desc
        (hub narrativi prima — più utili per il retrieval).
        """
        data = self._graph.get_all_nodes_and_edges()

        # Costruisce lookup id → { name, k_core } per efficienza
        node_lookup: dict[str, dict] = {}
        for node in data["nodes"]:
            nid = node.get("id")
            if nid:
                node_lookup[nid] = {
                    "name":   node.get("name") or nid,
                    "k_core": node.get("k_core") or 0,
                }

        triplets: list[Triplet] = []

        for edge in data["edges"]:
            from_id  = edge.get("from")
            to_id    = edge.get("to")
            rel_type = edge.get("type", "")

            if not from_id or not to_id:
                continue

            subject = node_lookup.get(from_id)
            obj     = node_lookup.get(to_id)

            if not subject or not obj:
                logger.warning(
                    "Edge %s→%s: node not found in lookup, skipping", from_id, to_id
                )
                continue

            predicate = RELATION_TO_PREDICATE.get(rel_type)
            if predicate is None:
                logger.debug("No predicate mapping for relation type '%s'", rel_type)
                continue

            triplets.append(
                Triplet(
                    subject_id=from_id,
                    subject_name=subject["name"],
                    predicate=predicate,
                    object_id=to_id,
                    object_name=obj["name"],
                    relation_type=rel_type,
                    relation_properties=edge.get("props", {}),
                    subject_kcore=subject["k_core"],
                    object_kcore=obj["k_core"],
                )
            )

        triplets.sort(key=lambda t: t.metadata["max_kcore"], reverse=True)
        logger.info("Extracted %d triplets from graph", len(triplets))
        return triplets

    def extract_for_nodes(self, node_ids: list[str]) -> list[Triplet]:
        """
        Estrae solo i triplets che coinvolgono i nodi specificati
        (come soggetto o oggetto). Usato dal retrieval per arricchire
        il contesto di nodi specifici senza scaricare tutto il grafo.
        """
        all_triplets = self.extract_all()
        node_set = set(node_ids)
        filtered = [
            t for t in all_triplets
            if t.subject_id in node_set or t.object_id in node_set
        ]
        logger.debug(
            "Filtered %d triplets for nodes %s", len(filtered), node_ids
        )
        return filtered

    def to_qdrant_documents(self, triplets: list[Triplet]) -> list[dict]:
        """
        Converte una lista di Triplet nel formato atteso da QdrantIngestor:
            { "text": ..., "metadata": ... }

        Chiamato da qdrant_ingestor.py prima dell'embedding.
        """
        return [
            {"text": t.text, "metadata": t.metadata}
            for t in triplets
        ]
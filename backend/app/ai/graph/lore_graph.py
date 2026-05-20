"""
lore_graph.py — Neo4j interface for Piombo Engine lore knowledge graph.

Responsabilità:
- Ingest dei seed lore JSON (characters, locations, factions, objects, events)
- Creazione nodi e relazioni su Neo4j
- Update k-core values post-calcolo NetworkX
- Query helpers usati dal retrieval layer
"""

import json
import logging
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase, Driver

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Label → file mapping (corrisponde ai file in backend/seed_lore/)
# ---------------------------------------------------------------------------
SEED_FILES: dict[str, str] = {
    "Character": "characters.json",
    "Location":  "locations.json",
    "Faction":   "factions.json",
    "Object":    "objects.json",
    "Event":     "events.json",
}

# ---------------------------------------------------------------------------
# Relazioni attese nei JSON (campo "relations" di ogni entità, opzionale)
# Formato atteso per ogni relazione:
#   { "type": "MEMBER_OF", "target_id": "faction_001", "properties": {} }
# ---------------------------------------------------------------------------
VALID_RELATION_TYPES = {
    "MEMBER_OF",
    "LOCATED_IN",
    "KNOWS",
    "POSSESSES",
    "INVOLVED_IN",
    "CONTROLLED_BY",
    "CONNECTED_TO",
    "HOSTILE_TO",
    "ALLIED_WITH",
    "OCCURRED_IN",
    "CAUSED_BY",
}


class LoreGraph:
    """
    Wrapper Neo4j per il knowledge graph di Piombo Engine.

    Uso tipico:
        graph = LoreGraph(uri="bolt://localhost:7687", user="neo4j", password="...")
        graph.ingest_all(seed_dir=Path("backend/seed_lore"))
        graph.update_kcore_values(kcore_map={"char_001": 3, "loc_002": 2, ...})
        subgraph = graph.get_neighborhood("char_001", depth=2)
    """

    def __init__(self, uri: str, user: str, password: str) -> None:
        self._driver: Driver = GraphDatabase.driver(uri, auth=(user, password))
        logger.info("LoreGraph connected to %s", uri)

    def close(self) -> None:
        self._driver.close()

    # ------------------------------------------------------------------
    # Setup: constraints e indici
    # ------------------------------------------------------------------
    def create_constraints(self) -> None:
        """
        Crea uniqueness constraints su (label, id).
        Idempotente — sicuro da rieseguire.
        """
        constraints = [
            ("Character", "id"),
            ("Location",  "id"),
            ("Faction",   "id"),
            ("Object",    "id"),
            ("Event",     "id"),
        ]
        with self._driver.session() as session:
            for label, prop in constraints:
                session.run(
                    f"CREATE CONSTRAINT IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
                )
        logger.info("Constraints created/verified")


    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------
    def ingest_all(self, seed_dir: Path) -> dict[str, int]:
        """
        Ingesta tutti i seed JSON in Neo4j.
        Ritorna un conteggio { label: n_nodi_creati }.
        """
        self.create_constraints()
        counts: dict[str, int] = {}

        for label, filename in SEED_FILES.items():
            filepath = seed_dir / filename
            if not filepath.exists():
                logger.warning("Seed file not found: %s — skipping", filepath)
                counts[label] = 0
                continue

            entities = json.loads(filepath.read_text(encoding="utf-8"))
            n = self._ingest_nodes(label, entities)
            counts[label] = n
            logger.info("Ingested %d %s nodes", n, label)

        # Seconda passata: relazioni (tutti i file caricati)
        total_rels = self._ingest_all_relations(seed_dir)
        logger.info("Ingested %d relations total", total_rels)

        return counts

    def _ingest_nodes(self, label: str, entities: list[dict]) -> int:
        """MERGE nodi per label. Usa tutti i campi scalari come proprietà."""
        query = (
            f"UNWIND $rows AS row "
            f"MERGE (n:{label} {{id: row.id}}) "
            f"SET n += row "          # sovrascrive / aggiunge tutte le proprietà
            f"RETURN count(n) AS cnt"
        )
        # Neo4j non accetta dict annidati come property — serializza a JSON string
        rows = [_flatten_entity(e) for e in entities]
        with self._driver.session() as session:
            result = session.run(query, rows=rows)
            return result.single()["cnt"]

    def _ingest_all_relations(self, seed_dir: Path) -> int:
        """
        Seconda passata su tutti i JSON per creare le relazioni.
        Ogni entità può avere un campo opzionale "relations": lista di dict.
        """
        total = 0
        for filename in SEED_FILES.values():
            filepath = seed_dir / filename
            if not filepath.exists():
                continue
            entities = json.loads(filepath.read_text(encoding="utf-8"))
            for entity in entities:
                rels = entity.get("relations", [])
                for rel in rels:
                    if self._create_relation(entity["id"], rel):
                        total += 1
        return total

    def _create_relation(self, source_id: str, rel: dict) -> bool:
        """
        Crea una singola relazione tipata tra due nodi (match by id).
        rel dict atteso: { type, target_id, properties? }
        """
        rel_type = rel.get("type", "").upper()
        if rel_type not in VALID_RELATION_TYPES:
            logger.warning("Unknown relation type '%s' — skipping", rel_type)
            return False

        target_id = rel.get("target_id")
        props = rel.get("properties", {})

        query = (
            f"MATCH (a {{id: $source_id}}), (b {{id: $target_id}}) "
            f"MERGE (a)-[r:{rel_type}]->(b) "
            f"SET r += $props "
            f"RETURN r"
        )
        with self._driver.session() as session:
            result = session.run(
                query,
                source_id=source_id,
                target_id=target_id,
                props=props,
            )
            return result.single() is not None

    # ------------------------------------------------------------------
    # Update k-core (chiamato dopo NetworkX ricalcolo)
    # ------------------------------------------------------------------
    def update_kcore_values(self, kcore_map: dict[str, int]) -> int:
        """
        Aggiorna la proprietà `k_core` su ogni nodo con i valori calcolati
        da NetworkX. kcore_map: { node_id: k_value }
        Ritorna il numero di nodi aggiornati.
        """
        rows = [{"id": nid, "k_core": k} for nid, k in kcore_map.items()]
        query = (
            "UNWIND $rows AS row "
            "MATCH (n {id: row.id}) "
            "SET n.k_core = row.k_core "
            "RETURN count(n) AS cnt"
        )
        with self._driver.session() as session:
            result = session.run(query, rows=rows)
            cnt = result.single()["cnt"]
        logger.info("Updated k_core on %d nodes", cnt)
        return cnt

    # ------------------------------------------------------------------
    # Query helpers — usati dal retrieval layer
    # ------------------------------------------------------------------

    def get_node(self, node_id: str) -> dict | None:
        """Ritorna un nodo by id con tutte le sue proprietà."""
        with self._driver.session() as session:
            result = session.run(
                "MATCH (n {id: $id}) RETURN properties(n) AS props, labels(n) AS labels",
                id=node_id,
            )
            record = result.single()
            if record is None:
                return None
            return {"labels": record["labels"], **record["props"]}

    def get_neighborhood(
        self,
        node_id: str,
        depth: int = 2,
        min_kcore: int = 0,
    ) -> dict[str, Any]:
        """
        Ritorna il sotto-grafo a `depth` hop da node_id.
        Filtra nodi con k_core < min_kcore (per risparmio token).
        Formato output: { nodes: [...], edges: [...] }
        """
        query = (
            "MATCH path = (start {id: $id})-[*1..$depth]-(neighbor) "
            "WHERE neighbor.k_core >= $min_kcore OR neighbor.k_core IS NULL "
            "UNWIND nodes(path) AS n "
            "UNWIND relationships(path) AS r "
            "RETURN DISTINCT "
            "  collect(DISTINCT {id: n.id, name: n.name, k_core: n.k_core, labels: labels(n)}) AS nodes, "
            "  collect(DISTINCT {from: startNode(r).id, to: endNode(r).id, type: type(r), props: properties(r)}) AS edges"
        )
        with self._driver.session() as session:
            result = session.run(query, id=node_id, depth=depth, min_kcore=min_kcore)
            record = result.single()
            if record is None:
                return {"nodes": [], "edges": []}
            return {"nodes": record["nodes"], "edges": record["edges"]}

    def get_nodes_by_ids(self, node_ids: list[str]) -> list[dict]:
        """Batch fetch di nodi per lista di id."""
        with self._driver.session() as session:
            result = session.run(
                "UNWIND $ids AS id "
                "MATCH (n {id: id}) "
                "RETURN properties(n) AS props, labels(n) AS labels",
                ids=node_ids,
            )
            return [
                {"labels": r["labels"], **r["props"]}
                for r in result
            ]

    def get_all_nodes_and_edges(self) -> dict[str, Any]:
        """
        Dump completo del grafo — usato da kcore.py per costruire
        il grafo NetworkX speculare.
        """
        with self._driver.session() as session:
            nodes_result = session.run(
                "MATCH (n) RETURN n.id AS id, labels(n) AS labels, n.k_core AS k_core"
            )
            nodes = [
                {"id": r["id"], "labels": r["labels"], "k_core": r["k_core"]}
                for r in nodes_result
                if r["id"] is not None
            ]
            edges_result = session.run(
                "MATCH (a)-[r]->(b) "
                "RETURN a.id AS from_id, b.id AS to_id, type(r) AS rel_type"
            )
            edges = [
                {"from": r["from_id"], "to": r["to_id"], "type": r["rel_type"]}
                for r in edges_result
            ]

        return {"nodes": nodes, "edges": edges}

    def search_nodes_by_name(self, name_query: str, limit: int = 5) -> list[dict]:
        """
        Ricerca fuzzy per nome — usata dall'entity linker a query time.
        Case-insensitive, match parziale.
        """
        with self._driver.session() as session:
            result = session.run(
                "MATCH (n) "
                "WHERE toLower(n.name) CONTAINS toLower($query) "
                "RETURN properties(n) AS props, labels(n) AS labels "
                "ORDER BY n.k_core DESC "
                "LIMIT $limit",
                query=name_query,
                limit=limit,
            )
            return [
                {"labels": r["labels"], **r["props"]}
                for r in result
            ]

    def get_stats(self) -> dict[str, int]:
        """Stats rapide del grafo — usate dall'endpoint /admin/graph/stats."""
        with self._driver.session() as session:
            node_count = session.run("MATCH (n) RETURN count(n) AS cnt").single()["cnt"]
            rel_count  = session.run("MATCH ()-[r]->() RETURN count(r) AS cnt").single()["cnt"]
            kcore_dist = session.run(
                "MATCH (n) WHERE n.k_core IS NOT NULL "
                "RETURN n.k_core AS k, count(n) AS cnt ORDER BY k"
            )
            kcore = {str(r["k"]): r["cnt"] for r in kcore_dist}

        return {
            "nodes": node_count,
            "relations": rel_count,
            "kcore_distribution": kcore,
        }


# ---------------------------------------------------------------------------
# Helpers interni
# ---------------------------------------------------------------------------

def _flatten_entity(entity: dict) -> dict:
    """
    Neo4j non accetta dict o list annidati come property value.
    - dict annidati → serializzati a JSON string
    - list di scalari → mantenuti (Neo4j li supporta)
    - list di dict → serializzati a JSON string
    - campo "relations" → rimosso (gestito separatamente)
    """
    result: dict = {}
    for key, value in entity.items():
        if key == "relations":
            continue  # le relazioni le gestiamo in _ingest_all_relations
        if isinstance(value, dict):
            result[key] = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, list):
            if value and isinstance(value[0], dict):
                result[key] = json.dumps(value, ensure_ascii=False)
            else:
                result[key] = value  # lista di scalari: OK
        else:
            result[key] = value
    return result
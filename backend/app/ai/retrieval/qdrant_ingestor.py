"""
qdrant_ingestor.py — Embedding e ingest in Qdrant per Piombo Engine.

Responsabilità:
- Embeda chunk descrittivi (description dei nodi lore) con bge-m3
- Embeda triplets testuali estratti da TripletExtractor
- Carica tutto in Qdrant con payload strutturato per filtering
- Espone search() per il retrieval semantico usato da hybrid.py

Due collection Qdrant:
  - "lore_descriptions": chunk testuali dalle description dei nodi
  - "lore_triplets":     triplets relazionali (soggetto, predicato, oggetto)

Separare le collection permette a hybrid.py di pesare diversamente
i due tipi di risultato e di filtrare per tipo a query time.
"""

import logging
import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    VectorParams,
    Filter,
    FieldCondition,
    MatchValue,
    Range,
)
from sentence_transformers import SentenceTransformer

from app.ai.graph.lore_graph import LoreGraph
from app.ai.retrieval.triplet import TripletExtractor, Triplet

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Costanti
# ---------------------------------------------------------------------------
COLLECTION_DESCRIPTIONS = "lore_descriptions"
COLLECTION_TRIPLETS     = "lore_triplets"

# bge-m3 produce vettori da 1024 dimensioni
VECTOR_SIZE = 1024

# Batch size per l'embedding (sentence-transformers)
EMBED_BATCH_SIZE = 32


class QdrantIngestor:
    """
    Gestisce embedding e ingest del lore in Qdrant.

    Uso tipico (post-ingest Neo4j, una tantum):
        ingestor = QdrantIngestor(qdrant_url="http://localhost:6333")
        ingestor.ingest_all(lore_graph, triplet_extractor)

    Uso runtime (retrieval):
        results = ingestor.search_descriptions("checkpoint autostradale", top_k=5)
        results = ingestor.search_triplets("Marco Ferretti", top_k=5, min_kcore=2)
    """

    def __init__(
        self,
        qdrant_url: str = "http://localhost:6333",
        embedding_model: str = "BAAI/bge-m3",
        device: str = "cuda",
    ) -> None:
        self._client = QdrantClient(url=qdrant_url)
        logger.info("Loading embedding model %s on %s...", embedding_model, device)
        self._encoder = SentenceTransformer(embedding_model, device=device)
        logger.info("QdrantIngestor ready")

    # ------------------------------------------------------------------
    # Setup collections
    # ------------------------------------------------------------------

    def create_collections(self, recreate: bool = False) -> None:
        """
        Crea le collection Qdrant. Se recreate=True, elimina e ricrea
        (utile per re-ingest completo durante sviluppo).
        """
        for name in (COLLECTION_DESCRIPTIONS, COLLECTION_TRIPLETS):
            exists = self._client.collection_exists(name)
            if exists and recreate:
                self._client.delete_collection(name)
                logger.info("Deleted collection '%s'", name)
                exists = False

            if not exists:
                self._client.create_collection(
                    collection_name=name,
                    vectors_config=VectorParams(
                        size=VECTOR_SIZE,
                        distance=Distance.COSINE,
                    ),
                )
                logger.info("Created collection '%s'", name)
            else:
                logger.info("Collection '%s' already exists — skipping", name)

    # ------------------------------------------------------------------
    # Ingest pipeline
    # ------------------------------------------------------------------

    def ingest_all(
        self,
        lore_graph: LoreGraph,
        triplet_extractor: TripletExtractor,
        recreate: bool = False,
    ) -> dict[str, int]:
        """
        Pipeline completa:
          1. Crea/ricrea le collection
          2. Embeda e carica le description dei nodi
          3. Estrae triplets e li embeda e carica

        Ritorna { collection: n_punti_inseriti }.
        """
        self.create_collections(recreate=recreate)

        n_desc     = self._ingest_descriptions(lore_graph)
        triplets   = triplet_extractor.extract_all()
        n_triplets = self._ingest_triplets(triplets)

        logger.info(
            "Ingest complete: %d descriptions, %d triplets",
            n_desc, n_triplets,
        )
        return {
            COLLECTION_DESCRIPTIONS: n_desc,
            COLLECTION_TRIPLETS:     n_triplets,
        }

    def _ingest_descriptions(self, lore_graph: LoreGraph) -> int:
        """
        Costruisce chunk descrittivi da ogni nodo del grafo e li embeda.

        Formato chunk:
            "[Label] Nome: description"
        Esempio:
            "[Character] Marco Ferretti: Ex medico di base, ora operatore
             clandestino della Colonna dei Sopravvissuti..."
        """
        data   = lore_graph.get_all_nodes_and_edges()
        chunks = []

        for node in data["nodes"]:
            nid         = node.get("id")
            name        = node.get("name") or nid
            description = node.get("description", "")
            labels      = node.get("labels") or []
            label_str   = labels[0] if labels else "Entity"

            if not description:
                continue

            text = f"[{label_str}] {name}: {description}"
            metadata = {
                "type":        "description",
                "node_id":     nid,
                "node_name":   name,
                "node_label":  label_str,
                "k_core":      node.get("k_core") or 0,
            }
            chunks.append({"text": text, "metadata": metadata})

        return self._upsert_documents(COLLECTION_DESCRIPTIONS, chunks)

    def _ingest_triplets(self, triplets: list[Triplet]) -> int:
        """Embeda e carica i triplets nella collection dedicata."""
        extractor = TripletExtractor.__new__(TripletExtractor)  # solo per to_qdrant_documents
        docs = [{"text": t.text, "metadata": t.metadata} for t in triplets]
        return self._upsert_documents(COLLECTION_TRIPLETS, docs)

    def _upsert_documents(
        self,
        collection_name: str,
        documents: list[dict],
    ) -> int:
        """
        Embeda una lista di { text, metadata } e fa upsert in Qdrant.
        Processa in batch per non saturare la VRAM.
        """
        if not documents:
            return 0

        texts = [d["text"] for d in documents]
        vectors = self._embed_batch(texts)

        points = [
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vectors[i].tolist(),
                payload=documents[i]["metadata"],
            )
            for i in range(len(documents))
        ]

        # Upsert in batch
        batch_size = 100
        for i in range(0, len(points), batch_size):
            self._client.upsert(
                collection_name=collection_name,
                points=points[i : i + batch_size],
            )

        logger.info(
            "Upserted %d points into '%s'", len(points), collection_name
        )
        return len(points)

    def _embed_batch(self, texts: list[str]):
        """
        Embeda una lista di testi con bge-m3.
        Usa encode() con batch_size per gestire liste grandi
        senza OOM sulla VRAM.
        """
        return self._encoder.encode(
            texts,
            batch_size=EMBED_BATCH_SIZE,
            show_progress_bar=len(texts) > 50,
            normalize_embeddings=True,  # cosine similarity richiede vettori normalizzati
        )

    # ------------------------------------------------------------------
    # Search — usato da hybrid.py
    # ------------------------------------------------------------------

    def search_descriptions(
        self,
        query: str,
        top_k: int = 5,
        min_kcore: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Ricerca semantica sui chunk descrittivi.
        Filtra per k_core >= min_kcore se specificato.
        """
        return self._search(
            collection=COLLECTION_DESCRIPTIONS,
            query=query,
            top_k=top_k,
            min_kcore=min_kcore,
        )

    def search_triplets(
        self,
        query: str,
        top_k: int = 5,
        min_kcore: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Ricerca semantica sui triplets relazionali.
        Filtra per max_kcore >= min_kcore se specificato.
        """
        return self._search(
            collection=COLLECTION_TRIPLETS,
            query=query,
            top_k=top_k,
            min_kcore=min_kcore,
            kcore_field="max_kcore",
        )

    def _search(
        self,
        collection: str,
        query: str,
        top_k: int,
        min_kcore: int = 0,
        kcore_field: str = "k_core",
    ) -> list[dict[str, Any]]:
        """
        Ricerca vettoriale con filtro k_core opzionale.
        Ritorna lista di { score, text (non disponibile), metadata }.
        """
        query_vector = self._embed_batch([query])[0].tolist()

        qdrant_filter = None
        if min_kcore > 0:
            qdrant_filter = Filter(
                must=[
                    FieldCondition(
                        key=kcore_field,
                        range=Range(gte=min_kcore),
                    )
                ]
            )

        response = self._client.query_points(
            collection_name=collection,
            query=query_vector,
            limit=top_k,
            query_filter=qdrant_filter,
            with_payload=True,
        )

        return [
            {
                "score":    r.score,
                "metadata": r.payload,
            }
            for r in response.points
        ]

    def get_collection_stats(self) -> dict[str, Any]:
        """Stats delle collection — usato da /admin/metrics."""
        stats = {}
        for name in (COLLECTION_DESCRIPTIONS, COLLECTION_TRIPLETS):
            try:
                info = self._client.get_collection(name)
                stats[name] = {
                    "points":     info.points_count,
                    "vector_size": VECTOR_SIZE,
                    "status":     str(info.status),
                }
            except Exception as e:
                stats[name] = {"error": str(e)}
        return stats
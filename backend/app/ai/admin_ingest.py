"""
admin_ingest.py — Pipeline di ingest completa per Piombo Engine.

Esegue in sequenza:
  1. Ingest seed lore JSON → Neo4j (nodi + relazioni)
  2. K-core decomposition reale → aggiorna Neo4j
  3. Estrazione triplets deterministici
  4. Embedding + ingest → Qdrant (descriptions + triplets)

Può essere chiamato:
  - Da CLI: python -m app.ai.admin_ingest
  - Da FastAPI: POST /admin/lore/ingest (via run_ingest_pipeline())
"""

import asyncio
import logging
import time
from pathlib import Path

from app.core.config import settings
from app.ai.graph.lore_graph import LoreGraph
from app.ai.graph.kcore import KCoreAnalyzer
from app.ai.retrieval.triplet import TripletExtractor
from app.ai.retrieval.qdrant_ingestor import QdrantIngestor

logger = logging.getLogger(__name__)


def run_ingest_pipeline(
    seed_dir: Path | None = None,
    recreate_qdrant: bool = False,
) -> dict:
    """
    Pipeline completa di ingest. Chiamata dall'endpoint /admin/lore/ingest
    e dal CLI.

    Args:
        seed_dir:        Path alla cartella seed_lore. Default da settings.
        recreate_qdrant: Se True, elimina e ricrea le collection Qdrant
                         (utile per re-ingest completo durante sviluppo).

    Returns:
        Dizionario con statistiche dell'ingest.
    """
    seed_dir = seed_dir or Path(settings.SEED_LORE_DIR)
    start    = time.time()

    logger.info("=== Piombo Engine — Ingest Pipeline ===")
    logger.info("Seed dir: %s", seed_dir)
    logger.info("Recreate Qdrant: %s", recreate_qdrant)

    # ------------------------------------------------------------------
    # Step 1 — Neo4j ingest
    # ------------------------------------------------------------------
    logger.info("[1/4] Ingesting seed lore into Neo4j...")
    t0 = time.time()

    graph = LoreGraph(
        uri=settings.NEO4J_URI,
        user=settings.NEO4J_USER,
        password=settings.NEO4J_PASSWORD,
    )
    neo4j_counts = graph.ingest_all(seed_dir)

    logger.info(
        "[1/4] Done in %.1fs — %s",
        time.time() - t0,
        neo4j_counts,
    )

    # ------------------------------------------------------------------
    # Step 2 — K-core decomposition
    # ------------------------------------------------------------------
    logger.info("[2/4] Computing k-core decomposition...")
    t0 = time.time()

    analyzer  = KCoreAnalyzer(graph)
    kcore_map = analyzer.compute_and_persist()

    kcore_stats = analyzer.get_subgraph_stats(min_k=0)
    logger.info(
        "[2/4] Done in %.1fs — %d nodes, distribution: %s",
        time.time() - t0,
        len(kcore_map),
        _kcore_distribution(kcore_map),
    )

    # ------------------------------------------------------------------
    # Step 3 + 4 — Triplets + Qdrant
    # ------------------------------------------------------------------
    logger.info("[3/4] Extracting triplets...")
    t0 = time.time()

    extractor = TripletExtractor(graph)
    triplets  = extractor.extract_all()

    logger.info("[3/4] Done in %.1fs — %d triplets", time.time() - t0, len(triplets))

    logger.info("[4/4] Embedding and ingesting into Qdrant...")
    t0 = time.time()

    ingestor      = QdrantIngestor(
        qdrant_url=settings.QDRANT_URL,
        embedding_model=settings.EMBEDDING_MODEL,
        device=settings.EMBEDDING_DEVICE,
    )
    qdrant_counts = ingestor.ingest_all(
        lore_graph=graph,
        triplet_extractor=extractor,
        recreate=recreate_qdrant,
    )

    logger.info("[4/4] Done in %.1fs — %s", time.time() - t0, qdrant_counts)

    # ------------------------------------------------------------------
    # Riepilogo
    # ------------------------------------------------------------------
    elapsed = time.time() - start
    graph_stats = graph.get_stats()

    summary = {
        "elapsed_seconds": round(elapsed, 1),
        "neo4j": {
            "nodes_per_label": neo4j_counts,
            "total_nodes":     graph_stats["nodes"],
            "total_relations": graph_stats["relations"],
            "kcore_distribution": graph_stats["kcore_distribution"],
        },
        "triplets": len(triplets),
        "qdrant":   qdrant_counts,
    }

    logger.info("=== Ingest complete in %.1fs ===", elapsed)
    logger.info("%s", summary)

    graph.close()
    return summary


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _kcore_distribution(kcore_map: dict[str, int]) -> dict[int, int]:
    dist: dict[int, int] = {}
    for k in kcore_map.values():
        dist[k] = dist.get(k, 0) + 1
    return dict(sorted(dist.items()))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    parser = argparse.ArgumentParser(description="Piombo Engine — Lore Ingest Pipeline")
    parser.add_argument(
        "--seed-dir",
        type=Path,
        default=None,
        help="Path alla cartella seed_lore (default: da settings)",
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Elimina e ricrea le collection Qdrant prima dell'ingest",
    )
    args = parser.parse_args()

    result = run_ingest_pipeline(
        seed_dir=args.seed_dir,
        recreate_qdrant=args.recreate,
    )

    print("\n=== Summary ===")
    for k, v in result.items():
        print(f"  {k}: {v}")
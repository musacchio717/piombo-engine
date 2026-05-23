"""
test_narrator_full.py — Test end-to-end del Narrator Agent.

Usa i moduli esistenti: OllamaLLM, HybridRetriever, build_narrator_graph.
Lancia da: cd ~/piombo-engine/backend && python test_narrator_full.py
"""

import logging
import time
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")

# ---------------------------------------------------------------------------
# Import moduli esistenti
# ---------------------------------------------------------------------------
from app.ai.graph import kcore
from app.core.config import settings
from app.ai.llm_client import OllamaLLM
from app.ai.graph.lore_graph import LoreGraph
from app.ai.graph.pagerank import PersonalizedPageRank
from app.ai.retrieval.qdrant_ingestor import QdrantIngestor
from app.ai.retrieval.hybrid import HybridRetriever
from app.ai.agents.narrator import build_narrator_graph, NarratorState
from app.ai.graph.kcore import KCoreAnalyzer
from app.ai.prompts.narrator import NARRATOR_SYSTEM_PROMPT
from app.ai.context_builder import build_core_context

# ---------------------------------------------------------------------------
# Inizializzazione componenti
# ---------------------------------------------------------------------------
def build_components():
    print("[1/4] LoreGraph...")
    lore_graph = LoreGraph(
        uri=settings.NEO4J_URI,
        user=settings.NEO4J_USER,
        password=settings.NEO4J_PASSWORD,
    )

    print("[2/4] QdrantIngestor + PPR...")
    qdrant = QdrantIngestor(
    qdrant_url=settings.QDRANT_URL,
    embedding_model=settings.EMBEDDING_MODEL,
    device=settings.EMBEDDING_DEVICE,
    )
    kcore = KCoreAnalyzer(lore_graph)
    ppr = PersonalizedPageRank(lore_graph, kcore)

    print("[3/4] HybridRetriever...")
    retriever = HybridRetriever(qdrant=qdrant, ppr=ppr, lore_graph=lore_graph)

    print("[4/4] OllamaLLM...")
    llm = OllamaLLM()
    print(f"      Modello: {llm.model}\n")

    return retriever, llm, lore_graph


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------
def run_test(retriever, llm, lore_graph, action: str, location: str = "pavia"):
    graph = build_narrator_graph(
        retriever=retriever,
        llm=llm,
        lore_graph=lore_graph,
    )

    initial_state: NarratorState = {
        "player_input": action,
        "current_location_id": location,
        "character_stats": {"health": 100, "reputation": 0, "suspicion": 5},
        "system_prompt": NARRATOR_SYSTEM_PROMPT,
        "core_context": build_core_context(lore_graph, location),
        "retrieval_context": "",
        "raw_llm_output": "",
        "narrator_output": None,
        "retry_count": 0,
        "errors": [],
    }

    print(f"AZIONE: {action}")
    print(f"LOCATION: {location}\n")
    print("=" * 60)

    t0 = time.time()
    result = graph.invoke(initial_state)
    elapsed = time.time() - t0

    out = result["narrator_output"]
    if out:
        print(f"<think>\n{out.think or '—'}\n</think>\n")
        print(f"<action>{out.action}</action>")
        print(f"<stat_change>{out.stat_change}</stat_change>\n")
        print(f"<response>\n{out.response}\n</response>")
    else:
        print("OUTPUT NON VALIDO")
        print("Raw:", result.get("raw_llm_output", ""))

    print("=" * 60)
    print(f"Latenza: {elapsed:.1f}s | Errori: {result.get('errors', [])}")
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    retriever, llm, lore_graph = build_components()

    # Scenario 1 — Incipit
    run_test(
        retriever, llm, lore_graph,
        action="Sei confuso, cammini senza meta per le strade di Pavia. Cadi per terra. Apri gli occhi. Cosa vedi intorno a te?",
        location="pavia",
    )
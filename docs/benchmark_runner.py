"""
benchmark_runner.py — Esegue i 4 scenari su un modello LLM e salva i risultati.

Uso:
    python docs/benchmark_runner.py --model qwen3:8b
    python docs/benchmark_runner.py --model mistral-nemo:12b-instruct-2407-q4_K_M

I risultati vengono salvati in docs/results/<model_name>.json
"""

import sys
import os
import json
import time
import argparse
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../backend"))

from app.core.database import SessionLocal
from app.models.session import GameSession, SessionStatus
from app.models.character import Character
from app.ai.llm_client import OllamaLLM
from app.ai.graph.lore_graph import LoreGraph
from app.ai.graph.kcore import KCoreAnalyzer
from app.ai.graph.pagerank import PersonalizedPageRank
from app.ai.retrieval.qdrant_ingestor import QdrantIngestor
from app.ai.retrieval.hybrid import HybridRetriever
from app.ai.agents.narrator import build_narrator_graph
from app.services.game_service import GameService
from app.core.config import settings

SCENARIOS = {
    "scenario_1": {
        "name": "Azione semplice in location conosciuta",
        "actions": [
            "Guardo fuori dalla finestra. Vedo dei soldati in tuta protettiva per strada."
        ],
    },
    "scenario_2": {
        "name": "Entità multiple dal Knowledge Graph",
        "actions": [
            "Cerco il Tenente Marini al Checkpoint A1 Sud per chiedergli un lasciapassare."
        ],
    },
    "scenario_3": {
        "name": "Dialogo in italiano colloquiale",
        "actions": [
            "Chiedo al negoziante: 'Senti, hai qualcosa da mangiare? Pago bene.'"
        ],
    },
    "scenario_4": {
        "name": "Sequenza 5 azioni consecutive",
        "actions": [
            "Esco dall'appartamento e scendo le scale.",
            "Guardo la strada prima di uscire dal portone.",
            "Cammino veloce verso il mercato abbandonato.",
            "Sento dei passi dietro di me, mi giro di scatto.",
            "Vedo una figura incappucciata che mi fa cenno di seguirla.",
        ],
    },
}


def create_test_session(db) -> tuple:
    char = Character(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        name="Alessandro Rullo",
        health=100,
        reputation=0,
        suspicion=0,
    )
    db.add(char)
    db.flush()
    session = GameSession(
        id=uuid.uuid4(),
        character_id=char.id,
        current_location_id="starting_location",
        status=SessionStatus.ACTIVE,
    )
    db.add(session)
    char.session_id = session.id
    db.commit()
    return session, char


def run_benchmark(model_name: str) -> dict:
    print(f"\n{'='*60}")
    print(f"Benchmark: {model_name}")
    print(f"{'='*60}")

    db = SessionLocal()
    lore_graph = LoreGraph(settings.NEO4J_URI, settings.NEO4J_USER, settings.NEO4J_PASSWORD)
    qdrant = QdrantIngestor(settings.QDRANT_URL, settings.EMBEDDING_MODEL, settings.EMBEDDING_DEVICE)
    kcore = KCoreAnalyzer(lore_graph)
    ppr = PersonalizedPageRank(lore_graph, kcore)
    retriever = HybridRetriever(qdrant, ppr, lore_graph)
    llm = OllamaLLM(model=model_name)
    graph = build_narrator_graph(retriever, llm=llm, lore_graph=lore_graph)
    service = GameService(graph, db)

    results = {"model": model_name, "scenarios": {}}

    for scenario_id, scenario in SCENARIOS.items():
        print(f"\n--- {scenario['name']} ---")
        session, _ = create_test_session(db)
        scenario_results = []

        for i, action in enumerate(scenario["actions"]):
            print(f"  [{i+1}/{len(scenario['actions'])}] {action[:60]}...")
            result = service.process_action(
                session_id=session.id,
                player_input=action,
            )
            entry = {
                "action":      action,
                "response":    result["response"],
                "latency_ms":  result["latency_ms"],
                "xml_valid":   len(result["errors"]) == 0,
                "stat_delta":  result["stat_delta"],
                "errors":      result["errors"],
            }
            scenario_results.append(entry)
            print(f"     latency: {result['latency_ms']}ms | xml_valid: {entry['xml_valid']}")
            print(f"     response: {result['response'][:100]}...")

        results["scenarios"][scenario_id] = {
            "name":    scenario["name"],
            "entries": scenario_results,
        }

    db.close()
    lore_graph.close()
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="Nome modello Ollama")
    args = parser.parse_args()

    results = run_benchmark(args.model)

    os.makedirs("docs/results", exist_ok=True)
    safe_name = args.model.replace(":", "_").replace("/", "_")
    out_path = f"docs/results/{safe_name}.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nRisultati salvati in: {out_path}")


if __name__ == "__main__":
    main()

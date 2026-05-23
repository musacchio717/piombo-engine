"""
test_scene_runner_llm.py — Test end-to-end con narrator LLM reale.

Esegui da backend/:
    python test_scene_runner_llm.py
    python test_scene_runner_llm.py casa_davide          # parte da scena specifica
    USE_MOCK_LLM=1 python test_scene_runner_llm.py       # usa MockLLM (no Ollama)

Setup richiesto:
- Docker compose attivo (Neo4j + Qdrant)
- Ollama running con il modello in config (mistral-nemo:12b)
- Ingest fatto (python -m app.ai.admin_ingest)
- Scene in seed_lore/scenes/, flags in seed_lore/flags.json
"""

import os
import sys
import logging
from pathlib import Path

# Setup logging - solo INFO per i nostri moduli, WARNING per il resto
logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s [%(name)s] %(message)s",
)
logging.getLogger("app").setLevel(logging.INFO)

# --- Stack imports ---
from app.core.config import settings  # noqa: F401  (forza load .env)
from app.ai.llm_client import OllamaLLM, MockLLM
from app.ai.graph.lore_graph import LoreGraph
from app.ai.graph.pagerank import PersonalizedPageRank
from app.ai.retrieval.qdrant_ingestor import QdrantIngestor
from app.ai.retrieval.hybrid import HybridRetriever
from app.ai.beat_narrator import BeatNarrator
from app.ai.graph.kcore import KCoreAnalyzer

# --- Game layer ---
from app.game.scene_loader import SceneLoader, load_flags_manifest
from app.game.scene_runner import SceneRunner
from app.game.models import BeatContext, ExplorationResult


USE_MOCK = os.getenv("USE_MOCK_LLM", "0") == "1"
SEPARATOR = "=" * 70


def build_stack() -> BeatNarrator:
    print(f"Inizializzazione stack — mock={USE_MOCK}")

    llm = MockLLM() if USE_MOCK else OllamaLLM()

    lore_graph = LoreGraph(
        uri=settings.NEO4J_URI,
        user=settings.NEO4J_USER,
        password=settings.NEO4J_PASSWORD,
    )
    kcore = KCoreAnalyzer(lore_graph)
    qdrant = QdrantIngestor(
        qdrant_url=settings.QDRANT_URL,
        embedding_model=settings.EMBEDDING_MODEL,
        device=settings.EMBEDDING_DEVICE,
    )
    ppr = PersonalizedPageRank(lore_graph, kcore)
    retriever = HybridRetriever(qdrant, ppr, lore_graph)

    return BeatNarrator(llm, retriever, lore_graph)


def print_prose(beat_type: str, scene_title: str, prose: str) -> None:
    print()
    print(SEPARATOR)
    print(f"[{beat_type.upper()}]  {scene_title}")
    print(SEPARATOR)
    print(prose)
    print()


def print_state(ctx: BeatContext) -> None:
    s = ctx.game_state
    active = [k for k, v in s.flags.items() if v]
    print(f"  stats={s.stats}  inv={s.inventory}")
    print(f"  flags_attivi={active}")


def play_session(narrator: BeatNarrator, runner: SceneRunner, start_scene_id: str) -> None:
    ctx = runner.start_session("test_llm_001", start_scene_id)

    while not runner.is_finished():

        if ctx.beat_type == "arrival":
            prose = narrator.render(ctx)
            print_prose("ARRIVAL", ctx.scene_title, prose)
            print_state(ctx)
            input("\n(enter per continuare) ")
            ctx = runner.advance_beat()

        elif ctx.beat_type == "exploration":
            print(f"\n[ESPLORAZIONE] {ctx.scene_title}")
            print_state(ctx)
            while runner.state.exploration_budget_remaining > 0:
                action = input(
                    f"\nbudget {runner.state.exploration_budget_remaining} — "
                    "azione libera (o 'avanti' per saltare) > "
                ).strip()
                if action.lower() in ("avanti", "skip", "fine", ""):
                    break
                result = runner.handle_exploration_action(action)
                prose = narrator.render(ctx, exploration_result=result)
                print_prose(f"EXPLORATION/{result.type}", ctx.scene_title, prose)
            ctx = (
                runner.skip_exploration()
                if runner.state.current_beat == "exploration"
                else runner.advance_beat()
            )

        elif ctx.beat_type == "event":
            prose = narrator.render(ctx)
            print_prose("EVENT", ctx.scene_title, prose)
            print_state(ctx)
            input("\n(enter per continuare) ")
            ctx = runner.advance_beat()

        elif ctx.beat_type == "decision":
            print(f"\n[DECISIONE] {ctx.scene_title}")
            for i, c in enumerate(ctx.choices):
                print(f"  [{i}] {c.text}")
            while True:
                raw = input("\nscegli (numero) > ").strip()
                try:
                    idx = int(raw)
                    choice = ctx.choices[idx]
                    break
                except (ValueError, IndexError):
                    print("Numero non valido.")
            resolution_ctx = runner.make_choice(choice.id)
            prose = narrator.render(resolution_ctx)
            print_prose("RESOLUTION", ctx.scene_title, prose)
            print_state(resolution_ctx)
            input("\n(enter per prossima scena) ")
            ctx = runner.transition()
            if ctx is None:
                print("\n*** Game over — fine dei rami disponibili ***")
                break

    print("\n" + SEPARATOR)
    print("FINE SESSIONE")
    print(SEPARATOR)


def main():
    backend_root = Path(__file__).parent
    scenes_dir = backend_root / "seed_lore" / "scenes"
    flags_file = backend_root / "seed_lore" / "flags.json"

    scenes = SceneLoader(scenes_dir).load_all()
    flags_manifest = load_flags_manifest(flags_file)
    print(f"Caricate {len(scenes)} scene, {len(flags_manifest)} flag")

    narrator = build_stack()
    runner = SceneRunner(scenes, flags_manifest)

    start = sys.argv[1] if len(sys.argv) > 1 else "stazione_pavia_inoculazione"
    play_session(narrator, runner, start)


if __name__ == "__main__":
    main()
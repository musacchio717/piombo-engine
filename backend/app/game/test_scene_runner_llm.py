"""
test_scene_runner_llm.py — Test end-to-end con narrator LLM + IntentRouter.

Esegui da backend/:
    python test_scene_runner_llm.py
    python test_scene_runner_llm.py casa_davide
    USE_MOCK_LLM=1 python test_scene_runner_llm.py
"""

import os
import sys
import logging
from pathlib import Path

logging.basicConfig(level=logging.WARNING, format="%(levelname)s [%(name)s] %(message)s")
logging.getLogger("app").setLevel(logging.INFO)

from app.core.config import settings
from app.ai.llm_client import OllamaLLM, MockLLM
from app.ai.graph.lore_graph import LoreGraph
from app.ai.graph.kcore import KCoreAnalyzer
from app.ai.graph.pagerank import PersonalizedPageRank
from app.ai.retrieval.qdrant_ingestor import QdrantIngestor
from app.ai.retrieval.hybrid import HybridRetriever
from app.ai.beat_narrator import BeatNarrator
from app.ai.intent_router import IntentRouter

from app.game.scene_loader import SceneLoader, load_flags_manifest
from app.game.scene_runner import SceneRunner
from app.game.models import BeatContext

USE_MOCK = os.getenv("USE_MOCK_LLM", "0") == "1"
SEP = "=" * 70


def build_stack():
    print(f"Inizializzazione stack — mock={USE_MOCK}")

    main_llm = MockLLM() if USE_MOCK else OllamaLLM()

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

    narrator = BeatNarrator(main_llm, retriever, lore_graph)

    # IntentRouter usa il modello checker (più piccolo e veloce)
    if USE_MOCK:
        router_llm = MockLLM()
    else:
        router_llm = OllamaLLM(model=settings.CHECKER_MODEL)
    router = IntentRouter(router_llm)

    return narrator, router


def print_prose(beat_label: str, scene_title: str, prose: str) -> None:
    print(f"\n{SEP}")
    print(f"[{beat_label}]  {scene_title}")
    print(SEP)
    print(prose)


def print_state(ctx: BeatContext) -> None:
    s = ctx.game_state
    active = [k for k, v in s.flags.items() if v]
    print(f"  stats={s.stats}  inv={s.inventory}")
    print(f"  flags={active}")


def play(narrator: BeatNarrator, router: IntentRouter,
         runner: SceneRunner, start_scene: str) -> None:

    ctx = runner.start_session("test_llm_001", start_scene)

    while not runner.is_finished():

        # ---- ARRIVAL ----
        if ctx.beat_type == "arrival":
            prose = narrator.render(ctx)
            print_prose("ARRIVAL", ctx.scene_title, prose)
            print_state(ctx)
            input("\n(enter) ")
            ctx = runner.advance_beat()

        # ---- EXPLORATION ----
        elif ctx.beat_type == "exploration":
            scene = runner.scenes[runner.state.current_scene_id]
            print(f"\n[ESPLORAZIONE] {ctx.scene_title}")
            print_state(ctx)

            while runner.state.exploration_budget_remaining > 0:
                action = input(
                    f"\nbudget {runner.state.exploration_budget_remaining} > "
                ).strip()

                if action.lower() in ("avanti", "skip", "fine", ""):
                    break

                # Classifica intento con LLM
                if scene.exploration:
                    result = router.classify(action, ctx, scene.exploration)
                    result = runner.consume_exploration_action(result)
                else:
                    # Fallback se non c'è exploration beat (non dovrebbe succedere)
                    result = runner.handle_exploration_action(action)

                prose = narrator.render(ctx, exploration_result=result)
                print_prose(f"EXPLORATION/{result.type.upper()}", ctx.scene_title, prose)

            ctx = (
                runner.skip_exploration()
                if runner.state.current_beat == "exploration"
                else runner.advance_beat()
            )

        # ---- EVENT ----
        elif ctx.beat_type == "event":
            prose = narrator.render(ctx)
            print_prose("EVENT", ctx.scene_title, prose)
            print_state(ctx)
            input("\n(enter) ")
            ctx = runner.advance_beat()

        # ---- DECISION ----
        elif ctx.beat_type == "decision":
            print(f"\n[DECISIONE] {ctx.scene_title}")
            for i, c in enumerate(ctx.choices):
                print(f"  [{i}] {c.text}")

            while True:
                raw = input("\nscegli > ").strip()
                try:
                    choice = ctx.choices[int(raw)]
                    break
                except (ValueError, IndexError):
                    print("Numero non valido.")

            res_ctx = runner.make_choice(choice.id)
            prose = narrator.render(res_ctx)
            print_prose("RESOLUTION", ctx.scene_title, prose)
            print_state(res_ctx)
            input("\n(enter) ")

            ctx = runner.transition()
            if ctx is None:
                print("\n*** Fine dei rami disponibili ***")
                break

    print(f"\n{SEP}\nFINE SESSIONE\n{SEP}")


def main():
    backend_root = Path(__file__).parent
    scenes_dir = backend_root / "seed_lore" / "scenes"
    flags_file = backend_root / "seed_lore" / "flags.json"

    scenes = SceneLoader(scenes_dir).load_all()
    flags_manifest = load_flags_manifest(flags_file)
    print(f"Caricate {len(scenes)} scene, {len(flags_manifest)} flag")

    narrator, router = build_stack()
    runner = SceneRunner(scenes, flags_manifest)

    start = sys.argv[1] if len(sys.argv) > 1 else "stazione_pavia_inoculazione"
    play(narrator, router, runner, start)


if __name__ == "__main__":
    main()